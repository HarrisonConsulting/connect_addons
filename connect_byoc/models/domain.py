# -*- coding: utf-8 -*-

import logging
import re
from urllib.parse import urljoin
from odoo import fields, models, api, release
from odoo.exceptions import UserError, ValidationError
import phonenumbers
from twilio.twiml.voice_response import Client, Dial, VoiceResponse
from odoo.addons.connect.models.settings import format_connect_response, debug
from odoo.addons.connect_twilio.models.twiml import pretty_xml


logger = logging.getLogger(__name__)


def have_same_country_code(num1: str, num2: str) -> bool:
    try:
        phone1 = phonenumbers.parse(num1, None)
        phone2 = phonenumbers.parse(num2, None)
        return phone1.country_code == phone2.country_code
    except phonenumbers.NumberParseException:
        return False


class Domain(models.Model):
    _inherit = "connect.twilio.domain"

    byoc = fields.Many2one('connect.byoc', string='BYOC', readonly=True)

    def _should_reconcile_credentials(self):
        # A BYOC domain's credential list holds the carrier's SIP account
        # (connect.byoc.sip_username), not end-user registrations — importing
        # it via sync() would create a phantom connect.user for the carrier.
        self.ensure_one()
        return super()._should_reconcile_credentials() and not self.byoc

    def get_byoc_caller_id_number(self, byoc, number, callerId):
        if byoc:
            if not callerId and byoc.default_callerid:
                debug(self, "Case 1: user's callerid not set. Use BYOC's callerid.")
                return byoc.default_callerid.number
            elif not callerId and not byoc.default_callerid:
                debug(self, "Case 2: user's callerid not set. Use system default.")
                default_number = self.env["connect.twilio.outgoing_callerid"].sudo().search(
                    [("is_default", "=", True)], limit=1
                )
                return default_number.number
            elif callerId and not byoc.default_callerid:
                debug(self, "Case 3: Using user's callerID.")
                return callerId
            elif callerId and byoc.default_callerid:
                if have_same_country_code(number, callerId):
                    debug(self, "Case 4: Same country, using user's caller ID.")
                    return callerId
                else:
                    debug(self, "Case 5: Different country, using BYOC's caller ID.")
                    return byoc.default_callerid.number
        else:
            # No BYOC for route, use user's callerid or default.
            if callerId:
                return callerId
            else:
                return self.env["connect.twilio.outgoing_callerid"].sudo().search(
                    [("is_default", "=", True)], limit=1).number

    def _external_call_route(self, number):
        route = super()._external_call_route(number)
        rule = self.env['connect.outgoing_rule'].sudo().find_rule(number)
        if not rule:
            raise UserError('No outgoing rule found for this destination.')
        route['rule'] = rule.sudo()
        return route

    def _external_call_caller_id(self, user, number, request, route):
        # Gate first: a forged CallerId raises here, before any BYOC case.
        resolved = super()._external_call_caller_id(user, number, request, route)
        rule = route.get('rule')
        if not (rule and rule.byoc):
            return resolved
        chosen = resolved if (request.get('CallerId') or (user and user.twilio_outgoing_callerid)) else False
        return self.get_byoc_caller_id_number(rule.byoc, number, chosen)

    def _external_call_number(self, dial, number, status_url, route):
        rule = route.get('rule')
        if not (rule and rule.byoc):
            return super()._external_call_number(dial, number, status_url, route)
        dial.number(
            rule._byoc_dial_number(number),
            byoc=rule.byoc.sid,
            statusCallback=status_url,
            statusCallbackEvent="initiated answered completed",
        )
