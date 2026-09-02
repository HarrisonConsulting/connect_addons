# -*- coding: utf-8 -*-
"""Read-only customer access to Connect call history.

Precedent: Odoo 19's sale portal controller uses ``CustomerPortal``, a
partner-scoped domain, ``portal_pager``, and sudoed records for rendering:
``/mnt/19/odoo/addons/sale/controllers/portal.py``.

This controller deliberately does not grant portal ACLs on Connect models.
Every record and media lookup applies ``connect.call._customer_portal_domain``
before sudoed data is returned.
"""

import logging
import os

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


_logger = logging.getLogger(__name__)


class ConnectCustomerPortal(CustomerPortal):
    """Expose exact-contact call history to authenticated portal customers."""

    def _customer_call_portal_enabled(self):
        return bool(
            request.env['connect.settings'].sudo().get_param(
                'customer_portal_calls_enabled', False
            )
        )

    def _customer_call_domain(self):
        return request.env['connect.call']._customer_portal_domain(
            request.env.user.partner_id
        )

    def _require_customer_call_portal(self):
        if not self._customer_call_portal_enabled():
            raise NotFound()

    def _get_customer_call(self, call_id):
        self._require_customer_call_portal()
        domain = [('id', '=', int(call_id)), *self._customer_call_domain()]
        call = request.env['connect.call'].sudo().search(domain, limit=1)
        if not call:
            raise NotFound()
        return call

    def _prepare_portal_layout_values(self):
        values = super()._prepare_portal_layout_values()
        values['connect_customer_call_portal_enabled'] = (
            self._customer_call_portal_enabled()
        )
        return values

    def _prepare_home_portal_values(self, counters):
        # Every key returned here is echoed by /my/counters, and the portal's
        # counter interaction writes each one into a matching
        # [data-placeholder_count] element. A key without such an element is a
        # TypeError that aborts the batch and strands the loading spinner, so
        # only genuine counters belong in these values.
        values = super()._prepare_home_portal_values(counters)
        if 'connect_call_count' in counters and self._customer_call_portal_enabled():
            values['connect_call_count'] = request.env[
                'connect.call'
            ].sudo().search_count(self._customer_call_domain())
        return values

    @http.route(
        ['/my/calls', '/my/calls/page/<int:page>'],
        type='http', auth='user', website=True,
    )
    def portal_my_calls(
        self, page=1, sortby='date', filterby='all', search=None, **kw
    ):
        self._require_customer_call_portal()
        values = self._prepare_portal_layout_values()
        Call = request.env['connect.call'].sudo()

        searchbar_sortings = {
            'date': {'label': _('Newest'), 'order': 'create_date desc, id desc'},
            'oldest': {'label': _('Oldest'), 'order': 'create_date asc, id asc'},
        }
        searchbar_filters = {
            'all': {'label': _('All'), 'domain': []},
            'calls': {
                'label': _('Calls'),
                'domain': [('call_result', '!=', 'voicemail')],
            },
            'voicemails': {
                'label': _('Voicemails'),
                'domain': [('call_result', '=', 'voicemail')],
            },
        }
        if sortby not in searchbar_sortings:
            sortby = 'date'
        if filterby not in searchbar_filters:
            filterby = 'all'

        domain = [
            *self._customer_call_domain(),
            *searchbar_filters[filterby]['domain'],
        ]
        if search:
            domain += [
                '|',
                ('caller', 'ilike', search),
                ('called', 'ilike', search),
            ]

        total = Call.search_count(domain)
        pager = portal_pager(
            url='/my/calls',
            total=total,
            page=page,
            step=self._items_per_page,
            url_args={
                'sortby': sortby,
                'filterby': filterby,
                'search': search,
            },
        )
        calls = Call.search(
            domain,
            order=searchbar_sortings[sortby]['order'],
            limit=self._items_per_page,
            offset=pager['offset'],
        )
        request.session['my_connect_calls_history'] = calls.ids[:100]

        values.update({
            'calls': calls,
            'page_name': 'connect_calls',
            'pager': pager,
            'default_url': '/my/calls',
            'searchbar_sortings': searchbar_sortings,
            'searchbar_filters': searchbar_filters,
            'searchbar_inputs': {
                'number': {
                    'input': 'number',
                    'label': _('Search phone numbers'),
                },
            },
            'sortby': sortby,
            'filterby': filterby,
            'search_in': 'number',
            'search': search or '',
        })
        return request.render('connect.portal_my_calls', values)

    @http.route(
        '/my/calls/<int:call_id>', type='http', auth='user', website=True,
    )
    def portal_my_call(self, call_id, **kw):
        call = self._get_customer_call(call_id)
        values = self._prepare_portal_layout_values()
        values.update({
            'call': call,
            'recording': call.recording,
            'page_name': 'connect_call',
        })
        return request.render('connect.portal_my_call', values)

    @http.route(
        '/my/calls/<int:call_id>/recording',
        type='http', auth='user', website=True,
    )
    def portal_my_call_recording(self, call_id, **kw):
        call = self._get_customer_call(call_id)
        recording = call.recording
        if not recording:
            raise NotFound()
        mimetype = recording.attachment_id.mimetype or 'audio/mpeg'
        return self._serve_customer_audio(
            recording._download_recording_audio,
            mimetype,
            f'call_{call.id}_recording.mp3',
        )

    @http.route(
        '/my/calls/<int:call_id>/voicemail',
        type='http', auth='user', website=True,
    )
    def portal_my_call_voicemail(self, call_id, **kw):
        call = self._get_customer_call(call_id)
        if not (call.voicemail_attachment_id or call.voicemail_url):
            raise NotFound()
        mimetype = call.voicemail_attachment_id.mimetype or 'audio/mpeg'
        return self._serve_customer_audio(
            call._download_voicemail_audio,
            mimetype,
            f'call_{call.id}_voicemail.mp3',
        )

    def _serve_customer_audio(self, downloader, mimetype, filename):
        """Materialize provider/storage audio and return bytes without a URL."""
        path = None
        try:
            path = downloader()
            if not path:
                raise NotFound()
            with open(path, 'rb') as audio_file:
                data = audio_file.read()
        except NotFound:
            raise
        except Exception:
            _logger.exception('Customer portal audio download failed')
            raise NotFound()
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    _logger.warning('Could not remove temporary portal audio %s', path)

        if not data:
            raise NotFound()
        return request.make_response(data, headers=[
            ('Content-Type', mimetype),
            ('Content-Length', str(len(data))),
            ('Content-Disposition', http.content_disposition(filename)),
            ('Cache-Control', 'private, no-store'),
        ])
