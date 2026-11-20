import logging
import phonenumbers
import re
from phonenumbers import phonenumberutil
from odoo import models, fields, api, release
from .settings import debug

logger = logging.getLogger(__name__)

CALLER_CANDIDATE_LIMIT = 5


def _caller_name_key(partner):
    """Normalized name, or the record id when the name is blank."""
    name = ' '.join((partner.name or '').split()).casefold()
    return name or 'id:%s' % partner.id


def strip_number(number):
    if not isinstance(number, str):
        return number
    pattern = r'[\s\(\)\-\+]'
    return re.sub(pattern, '', number).lstrip('0')


def format_number(self, number, country=None, format_type='e164'):
    res = False
    try:
        phone_nbr = phonenumbers.parse(number, country or None)
        if not phonenumbers.is_possible_number(phone_nbr):
            debug(self, '{} country {} parse impossible'.format(
                number, country
            ))
        elif format_type == 'e164':
            res = phonenumbers.format_number(
                phone_nbr, phonenumbers.PhoneNumberFormat.E164)
        else:
            logger.error('WRONG FORMATTING PASSED: %s', format_type)
    except phonenumberutil.NumberParseException:
        debug(self, '{} {} {} got NumberParseException'.format(
            number, country, format_type
        ))
    except Exception:
        logger.exception('FORMAT NUMBER ERROR: ')
    finally:
        debug(self, '{} county {} format {}: {}'.format(
            number, country, format_type, res))
        return res or number


class Partner(models.Model):
    _inherit = 'res.partner'

    @api.model
    def create_record_from_message(self, message, default_values=None):
        number = message.from_number
        partner = self.get_partner_by_number(number)
        if partner:
            return partner
        vals = {
            'name': number,
            'phone': number,
        }
        if isinstance(default_values, dict):
            vals.update(default_values)
        return self.create(vals)

    connect_calls_count = fields.Integer(compute='_get_connect_calls_count')
    connect_messages_count = fields.Integer(compute='_get_connect_messages_count')
    connect_recorded_calls = fields.One2many('connect.recording', 'partner')
    connect_user = fields.Many2one('connect.user', compute='_get_connect_user')
    if release.version_info[0] >= 19:
        mobile = fields.Char()

    def _get_connect_user(self):
        for rec in self:
            user = self.env['res.users'].sudo().search([('partner_id', '=', rec.id)])
            rec.connect_user = user.connect_user

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        try:
            if self.env.context.get('connect_call_id'):
                call = self.env['connect.call'].sudo().browse(
                    self.env.context['connect_call_id'])
                call.partner = res[0]
        except Exception as e:
            logger.exception(e)
        # NB: no registry.clear_cache() here. There is no ormcache keyed on
        # partner data in this addon (connect_*_count are non-stored computes,
        # get_partner_by_number does a live search), so clearing the whole
        # ORM cache on every partner create/write/unlink was pure overhead
        # on a hot path. Removed; the ORM invalidates its own field caches.
        return res

    def _normalize_phone(self, number):
        if self.env['connect.settings'].sudo().get_param('disable_phone_format'):
            return number
        country = self._get_country()
        try:
            phone_nbr = phonenumbers.parse(number, country)
            if phonenumbers.is_possible_number(phone_nbr) or \
                    phonenumbers.is_valid_number(phone_nbr):
                number = phonenumbers.format_number(
                    phone_nbr, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.phonenumberutil.NumberParseException:
            number = '+{}'.format(strip_number(number))
        except Exception as e:
            logger.warning('Normalize phone error: %s', e)
        return number

    @api.model
    def _search_partners_by_number(self, number):
        """Contacts whose phone or mobile is this number. Empty when number is blank."""
        if not number:
            return self.env['res.partner']
        found = self.sudo().search([('phone_mobile_search', '=', number)])
        if not found:
            country = self.env['res.company'].sudo().browse(1).country_id.code
            normalized = format_number(self, number, country)
            if normalized and normalized != number:
                found = self.sudo().search(
                    [('phone_mobile_search', '=', normalized)])
                if not found and 'phone_sanitized' in self._fields:
                    found = self.sudo().search(
                        [('phone_sanitized', '=', normalized)])
                debug(self, '{} normalized to {} for partner lookup'.format(
                    number, normalized
                ))
            elif normalized and 'phone_sanitized' in self._fields:
                found = self.sudo().search(
                    [('phone_sanitized', '=', normalized)])
        debug(self, '{} belongs to partners: {}'.format(
            number, found.mapped('id')
        ))
        return found

    @api.model
    def _collapse_caller_candidates(self, found):
        """Distinct people this number could be.

        A company drops when one of its people is in the set. The same
        normalized name keeps the user, then the person, then the lowest id.
        """
        if not found:
            return found
        people = found.filtered(lambda partner: not partner.is_company)
        represented = people.commercial_partner_id | people.mapped('parent_id')
        bare = found.filtered(lambda partner: partner.is_company) - represented
        pool = people | bare
        if not pool:
            pool = found
        ranked = pool.sorted(key=lambda partner: (
            0 if partner.user_ids else 1,
            0 if not partner.is_company else 1,
            partner.id,
        ))
        seen = set()
        kept = []
        for partner in ranked:
            key = _caller_name_key(partner)
            if key in seen:
                continue
            seen.add(key)
            kept.append(partner.id)
        return self.sudo().browse(kept)

    @api.model
    def caller_candidates(self, number, limit=CALLER_CANDIDATE_LIMIT):
        """Up to ``limit`` distinct contacts for ``number``, and the full count."""
        distinct = self._collapse_caller_candidates(
            self._search_partners_by_number(number))
        cap = CALLER_CANDIDATE_LIMIT if limit is None else limit
        return distinct[:cap], len(distinct)

    @api.model
    def get_partner_by_number(self, number):
        """The one contact for this number, or empty when several people remain."""
        distinct = self._collapse_caller_candidates(
            self._search_partners_by_number(number))
        if len(distinct) == 1:
            return distinct
        if distinct:
            debug(self, '{} matches {} distinct callers'.format(
                number, distinct.ids))
        return self.env['res.partner']

    def _get_country(self):
        partner = self
        if partner and partner.country_id:
            return partner.country_id.code
        elif partner and partner.parent_id and partner.parent_id.country_id:
            return partner.parent_id.country_id.code
        elif partner and partner.company_id and partner.company_id.country_id:
            return partner.company_id.country_id.code
        elif self.env.user and self.env.user.company_id.country_id:
            return self.env.user.company_id.country_id.code

    def _get_connect_calls_count(self):
        for rec in self:
            if rec.is_company:
                rec.connect_calls_count = self.env[
                    'connect.call'].sudo().search_count(
                    ['|', ('partner', '=', rec.id),
                          ('partner.parent_id', '=', rec.id)])
            else:
                rec.connect_calls_count = self.env[
                    'connect.call'].sudo().search_count(
                    [('partner', '=', rec.id)])

    def _get_connect_messages_count(self):
        for rec in self:
            if rec.is_company:
                rec.connect_messages_count = self.env['connect.message'].sudo().search_count(
                    ['|', ('partner', '=', rec.id), ('partner.parent_id', '=', rec.id)]
                )
            else:
                rec.connect_messages_count = self.env['connect.message'].sudo().search_count(
                    [('partner', '=', rec.id)]
                )

    def _phone_format(self, number=None, country=None, company=None, force_format='E164', **kwargs):
        version_info = release.version_info
        if version_info[0] < 16:
            return super(Partner, self)._phone_format(number, country=country, company=company)
        elif version_info[0] == 16:
            return super(Partner, self)._phone_format(number, country=country, company=company, force_format=force_format)
        else:
            fname = kwargs.get('fname', False)
            return super(Partner, self)._phone_format(fname=fname, number=number, country=country, force_format=force_format)

    @api.model
    def api_get_partner(self, number):
        partner = self.get_partner_by_number(number)
        if partner:
            return {'id': partner.id, 'name': partner.display_name}
        else:
            return {'id': False, 'name': 'Unknown'}
