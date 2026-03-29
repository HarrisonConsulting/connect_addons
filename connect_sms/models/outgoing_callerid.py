import logging

from odoo import api, models
from odoo.addons.phone_validation.tools import phone_validation

_logger = logging.getLogger(__name__)


class OutgoingCallerID(models.Model):
    _inherit = 'connect.outgoing_callerid'

    @api.model
    def sync(self):
        super().sync()
        self._sync_to_sms_twilio_numbers()

    @api.model
    def _sync_to_sms_twilio_numbers(self):
        """Populate sms.twilio.number from connect.outgoing_callerid DID numbers.

        Only DID numbers (callerid_type='number') are synced because they are
        actual Twilio incoming phone numbers capable of sending SMS. Plain
        CallerIDs (callerid_type='outgoing_callerid') cannot send SMS.
        """
        did_numbers = self.sudo().search([('callerid_type', '=', 'number')])
        companies = self.env['res.company'].sudo().search([
            ('sms_provider', '=', 'twilio'),
        ])
        if not companies:
            return

        TwilioNumber = self.env['sms.twilio.number'].sudo()
        Country = self.env['res.country'].sudo()
        created = 0
        removed = 0

        for company in companies:
            existing = {rec.number: rec for rec in company.sms_twilio_number_ids}
            desired = set()

            for callerid in did_numbers:
                desired.add(callerid.number)
                if callerid.number in existing:
                    continue
                country_code = phone_validation.phone_get_country_code_for_number(
                    callerid.number
                )
                country = Country.search([('code', '=', country_code)], limit=1)
                if not country:
                    # Fall back to company country
                    country = company.country_id
                if not country:
                    _logger.warning(
                        'connect_sms: cannot determine country for %s, skipping',
                        callerid.number,
                    )
                    continue
                TwilioNumber.create({
                    'company_id': company.id,
                    'number': callerid.number,
                    'country_id': country.id,
                })
                created += 1

            # Remove sms.twilio.number records that no longer exist in connect
            stale = [rec for num, rec in existing.items() if num not in desired]
            for rec in stale:
                rec.unlink()
                removed += 1

        if created or removed:
            _logger.info(
                'connect_sms: synced sms.twilio.number — created %d, removed %d',
                created, removed,
            )
