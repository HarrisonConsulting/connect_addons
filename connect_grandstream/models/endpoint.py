# -*- coding: utf-8 -*-

from odoo import fields, models
from odoo.exceptions import ValidationError


class Endpoint(models.Model):
    _inherit = 'connect.endpoint'

    vendor = fields.Selection(
        selection_add=[('grandstream', 'Grandstream')],
        ondelete={'grandstream': 'set null'},
    )

    def account_ceiling(self):
        """Return 16 SIP accounts for a Grandstream endpoint.

        16 and 12 are the GRP261x/GRP262x/GRP263x administration guide's
        family description for the GRP2670 (12 line keys, up to 16 SIP
        accounts). They are not a count taken from a unit.
        """
        self.ensure_one()
        if self.vendor == 'grandstream':
            return 16
        return super().account_ceiling()

    def line_ceiling(self):
        """Return 12 line keys for a Grandstream endpoint.

        16 and 12 are the GRP261x/GRP262x/GRP263x administration guide's
        family description for the GRP2670 (12 line keys, up to 16 SIP
        accounts). They are not a count taken from a unit.
        """
        self.ensure_one()
        if self.vendor == 'grandstream':
            return 12
        return super().line_ceiling()

    def provisioning_values(self, account):
        """Return stored SIP values for one account on this endpoint.

        The dict carries the server, user, transport, and slot already on
        the account, and marks the account active. It includes no password,
        auth id, or token, and no second registrar. Nothing is sent to the
        phone.
        """
        self.ensure_one()
        account.ensure_one()
        if account not in self.account_ids:
            raise ValidationError("The account does not belong to this endpoint.")
        return {
            'sip_server': account.sip_server,
            'sip_user': account.sip_user,
            'transport': account.transport,
            'slot': account.slot,
            'account_active': True,
        }

    def apply_registration_event(self, account, event):
        """Record register, unregister, or register-failed on this account."""
        self.ensure_one()
        account.ensure_one()
        if account not in self.account_ids:
            raise ValidationError("The account does not belong to this endpoint.")
        if event == 'register':
            account.mark_registered()
        elif event == 'unregister':
            account.mark_unregistered()
        elif event == 'register-failed':
            account.mark_failed()
        else:
            raise ValidationError(
                "Unknown registration event. Expected register, unregister, "
                "or register-failed."
            )
