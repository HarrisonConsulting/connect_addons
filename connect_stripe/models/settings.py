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
        """Return the enabled (or test) Stripe payment.provider for this company.

        Multi-company-safe: prefers a provider scoped to the current env.company,
        falling back to companyless providers if none match.
        """
        if 'payment.provider' not in self.env:
            return self.env['payment.provider'].browse()
        Provider = self.env['payment.provider'].sudo()
        company_id = self.env.company.id
        provider = Provider.search([
            ('code', '=', 'stripe'),
            ('state', 'in', ('enabled', 'test')),
            ('company_id', '=', company_id),
        ], limit=1)
        if provider:
            return provider
        return Provider.search([
            ('code', '=', 'stripe'),
            ('state', 'in', ('enabled', 'test')),
        ], limit=1)
