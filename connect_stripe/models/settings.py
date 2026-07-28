# -*- coding: utf-8 -*-

import logging

from odoo import fields, models, api

logger = logging.getLogger(__name__)


class ConnectStripeSettings(models.Model):
    _inherit = 'connect.settings'

    stripe_provider_active = fields.Boolean(
        compute='_compute_stripe_provider_active',
        help='True when an enabled/test Odoo payment_stripe provider is configured.',
    )
    stripe_pay_connector_name = fields.Char(
        string='Stripe Pay Connector Name',
        help='Name of the Stripe Pay Connector configured in the Twilio Console (e.g. "StripeConnector").',
    )
    stripe_default_currency_id = fields.Many2one(
        'res.currency',
        string='Default Currency',
        help='Default currency proposed by the Take Payment dialog when none is specified.',
    )

    @api.depends()
    def _compute_stripe_provider_active(self):
        provider = self._get_odoo_stripe_provider()
        for rec in self:
            rec.stripe_provider_active = bool(provider)

    def _get_odoo_stripe_provider(self):
        """Return the enabled (or test) Stripe payment.provider for THIS company.

        Strictly company-scoped, and never a Stripe Connect *mediated* provider.
        Returns an empty recordset when this company has none; the caller
        (`connect.payment`) already raises a clear "provider must be installed and
        enabled" error, which is the right outcome — see below.

        **Why there is no cross-company fallback any more (4859 GL-11 / GL-21).**
        This previously fell back to a search with the `company_id` filter dropped,
        documented as "companyless providers". No such thing exists: core declares
        `payment.provider.company_id` as `required=True`
        (/mnt/19/odoo/addons/payment/models/payment_provider.py:53-55), so the fallback
        could only ever return **another company's** provider — i.e. silently capture
        and charge a DTMF card on the wrong legal entity's Stripe account. Each company
        that takes DTMF payments has its own provider, so the fallback never fired on
        the happy path; it only ever fired where it was wrong.

        **The mediated exclusion.** A Stripe Connect connect-mediated provider (4859)
        borrows the platform's key and targets a connected account. DTMF is explicitly
        NOT part of the Connect rollout (DELIVERY.md §3c): the card is tokenized by the
        Twilio `<Pay>` Stripe Pay Connector against whatever account the Twilio Console
        points at, which no Odoo change can control. Resolving a mediated provider here
        would send the charge to the connected account while the PM lives elsewhere.
        The exclusion is guarded on field presence because this module depends on
        `payment_stripe`, not on `le_stripe_moto_payments` where the field is defined.
        """
        if 'payment.provider' not in self.env:
            return self.env['payment.provider'].browse()
        Provider = self.env['payment.provider'].sudo()
        domain = [
            ('code', '=', 'stripe'),
            ('state', 'in', ('enabled', 'test')),
            ('company_id', '=', self.env.company.id),
        ]
        if 'stripe_connect_mediated' in Provider._fields:
            domain.append(('stripe_connect_mediated', '=', False))
        provider = Provider.search(domain, limit=1)
        if not provider:
            logger.warning(
                "connect_stripe: no non-mediated Stripe payment.provider in company %s "
                "(%s) — DTMF card capture is unavailable here. Configure one in this "
                "company rather than relying on another company's provider.",
                self.env.company.id, self.env.company.display_name)
        return provider
