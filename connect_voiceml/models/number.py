# -*- coding: utf-8 -*-
import logging
from urllib.parse import parse_qs, urljoin, urlsplit

from odoo import fields, models, api
from odoo.exceptions import ValidationError

from odoo.addons.connect.models.settings import debug
from .settings import format_connect_response

logger = logging.getLogger(__name__)

MAX_SYNC_PAGES = 1000


def fetch_all_pages(fetch, attr):
    """Walk every page of an SDK list call and return all items.

    ``fetch(**kwargs)`` returns a page object carrying ``next_page_uri`` and a
    list under ``attr``. Any failure propagates, so callers never act on a
    partial list.
    """
    items = []
    kwargs = {}
    seen_uris = set()
    for _i in range(MAX_SYNC_PAGES):
        page = fetch(**kwargs)
        items.extend(getattr(page, attr, None) or [])
        next_uri = getattr(page, 'next_page_uri', None)
        if not next_uri:
            return items
        if next_uri in seen_uris:
            raise ValidationError('VoiceML paging loop detected at %s' % next_uri)
        seen_uris.add(next_uri)
        query = parse_qs(urlsplit(next_uri).query)
        token = (query.get('PageToken') or query.get('page_token') or [None])[0]
        if token:
            kwargs = {'page_token': token}
        elif (query.get('Page') or query.get('page')):
            kwargs = {'page': int((query.get('Page') or query.get('page'))[0])}
        else:
            raise ValidationError('Cannot follow VoiceML next page %s' % next_uri)
    raise ValidationError('VoiceML list exceeded %s pages' % MAX_SYNC_PAGES)


class Number(models.Model):
    _name = 'connect.voiceml.number'
    _description = 'VoiceML Phone Number'
    _rec_name = 'phone_number'
    _order = 'phone_number'

    phone_number = fields.Char(required=True)
    friendly_name = fields.Char()
    destination = fields.Selection(selection=[
        ('user', 'User'),
        ('callflow', 'CallFlow'),
        ('application', 'TwiML'),
    ], ondelete='set null')
    callflow = fields.Many2one('connect.voiceml.callflow', ondelete='set null')
    user = fields.Many2one('connect.user', ondelete='set null')
    application = fields.Many2one('connect.voiceml.application', ondelete='set null')
    is_ignored = fields.Boolean('Ignored')
    sid = fields.Char()
    voice_url = fields.Char(compute='_get_voiceml_urls', compute_sudo=True)
    voice_fallback_url = fields.Char(compute='_get_voiceml_urls', compute_sudo=True)
    voice_status_url = fields.Char(compute='_get_voiceml_urls', compute_sudo=True)

    def _get_voiceml_urls(self):
        api_url = self.env['connect.settings'].get_param('api_url')
        fallback_url = self.env['connect.settings'].get_param('api_fallback_url')
        for rec in self:
            rec.voice_status_url = urljoin(api_url, 'voiceml/webhook/callstatus')
            rec.voice_url = urljoin(api_url, 'voiceml/webhook/number')
            rec.voice_fallback_url = (
                urljoin(fallback_url, 'voiceml/webhook/number') if fallback_url else ''
            )

    def update_number(self, client):
        self.ensure_one()
        if self.is_ignored:
            return
        api_url = self.env['connect.settings'].get_param('api_url')
        try:
            client.incoming_phone_numbers.update(
                self.sid,
                voice_url=self.voice_url,
                voice_method='POST',
                voice_fallback_url=self.voice_fallback_url,
                sms_url=urljoin(api_url, 'voiceml/webhook/message'),
                sms_method='POST',
            )
        except Exception as e:
            logger.exception('Number Update Exception:')
            raise ValidationError(format_connect_response(str(e)))

    def write(self, vals):
        if 'destination' in vals:
            for field in ['user', 'callflow', 'application']:
                if field != vals['destination']:
                    vals.update({field: None})
        res = super().write(vals)
        if not self.env["connect.settings"].get_param("voiceml_auto_sync"):
            return res
        client = self.env['connect.settings']._voiceml_client()
        for rec in self:
            if not self.env.context.get('skip_voiceml_sync'):
                rec.update_number(client)
        return res

    @api.model
    def sync(self):
        settings = self.env['connect.settings']
        client = settings._voiceml_client()
        # Complete fetch first: a failing page raises here, before any change.
        numbers = fetch_all_pages(
            client.incoming_phone_numbers.list, 'incoming_phone_numbers')
        seen = set()
        failures = []
        for number in numbers:
            seen.add(number.sid)
            try:
                with self.env.cr.savepoint():
                    rec = self.search([('sid', '=', number.sid)])
                    if not rec:
                        rec = self.create({
                            'phone_number': number.phone_number,
                            'sid': number.sid,
                            'friendly_name': number.friendly_name,
                        })
                    rec.update_number(client)
            except Exception as e:
                logger.exception('Number sync failed for %s', number.sid)
                failures.append('%s: %s' % (number.sid, format_connect_response(str(e))))
        held = self.search([('sid', '!=', False)])
        to_remove = held.filtered(lambda r: r.sid not in seen)
        if to_remove:
            if not seen or len(to_remove) * 2 >= len(held):
                settings.connect_notify(
                    'Number sync skipped removal: VoiceML returned %s numbers, '
                    '%s of %s held numbers would be removed.'
                    % (len(seen), len(to_remove), len(held)),
                    title='Number Sync', warning=True)
            else:
                to_remove.unlink()
        if failures:
            settings.connect_notify(
                '%s number update(s) failed: %s' % (len(failures), '; '.join(failures)),
                title='Number Sync', warning=True)

    def render(self, request={}, params={}):
        self.ensure_one()
        if self.destination == 'application' and self.application:
            return self.application.render(request)
        elif self.destination == 'user' and self.user:
            return self.user.render_voiceml(request)
        elif self.destination == 'callflow' and self.callflow:
            return self.callflow.render(request)
        else:
            return '<Response><Say>Number not configured. Goodbye!</Say></Response>'

    @api.model
    def _route_call(self, request, params={}):
        debug(self, 'Route number call: %s' % request)
        self.env['connect.call']._on_call_status_voiceml(request)
        number = self.sudo().search([('phone_number', '=', request.get('Called'))])
        if not number:
            return '<Response><Say>Number not found. Goodbye!</Say></Response>'
        return number.render(request=request, params=params)
