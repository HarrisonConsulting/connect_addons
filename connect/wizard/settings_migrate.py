# -*- coding: utf-8 -*-

import logging
from markupsafe import escape
from twilio.rest import Client
from odoo import fields, models
from odoo.exceptions import UserError
from ..migrators import NumberMigrator, TwimlMigrator, DomainMigrator
from ..models.settings import _RewriteHostHttpClient, format_connect_response

logger = logging.getLogger(__name__)

# Order doesn't encode a Twilio-side dependency (numbers, TwiML apps and SIP
# domains are independent resource types) — numbers first only because it's
# the most commonly hit path and gives the fastest signal.
MIGRATORS = [NumberMigrator, TwimlMigrator, DomainMigrator]
MIGRATOR_TITLES = {
    'numbers': 'Numbers',
    'twiml_apps': 'TwiML Apps',
    'domains': 'SIP Domains',
}


class SettingsMigrateWizard(models.TransientModel):
    _name = 'connect.settings.migrate.wizard'
    _description = 'Migrate Account Wizard'

    target_account_sid = fields.Char(
        string='Target Account SID', required=True,
        help="Account SID of the destination account (Twilio or a "
             "Twilio-API-compatible provider).")
    target_auth_token = fields.Char(
        string='Target Auth Token', required=True,
        help="Auth token of the destination account. Only used by this "
             "wizard; it becomes the active auth token after migration.")
    target_rest_api_host = fields.Char(
        string='Target REST API Host',
        help="Custom REST host of a Twilio-API-compatible provider "
             "(e.g. voiceml.voicetel.com). Leave empty for Twilio.")
    state = fields.Selection(
        [('draft', 'Draft'), ('preview', 'Preview'), ('done', 'Done')],
        default='draft', required=True,
        help="Draft: enter target credentials. Preview: dry-run results are "
             "shown, nothing was changed. Done: numbers, TwiML apps and SIP "
             "domains migrated and Connect switched to the target account.")
    result = fields.Html(
        readonly=True,
        help="Per-resource outcome of the dry run or migration, plus the "
             "manual follow-up steps after cutover.")

    def _get_target_client(self):
        self.ensure_one()
        host = (self.target_rest_api_host or '').strip()
        http_client = _RewriteHostHttpClient(host) if host else None
        client = Client(self.target_account_sid, self.target_auth_token,
                        http_client=http_client)
        try:
            client.api.accounts(self.target_account_sid).fetch()
        except Exception as e:
            logger.exception('Target account validation failed:')
            raise UserError(
                'Could not authenticate against the target account: {}'.format(
                    format_connect_response(str(e))))
        return client

    def _render_result(self, results):
        def section(title, items, css=''):
            if not items:
                return ''
            rows = ''.join('<li>{}</li>'.format(escape(k)) for k in items)
            return '<h5 class="{}">{} ({})</h5><ul>{}</ul>'.format(
                css, escape(title), len(items), rows)

        html = ''
        for res in results:
            html += '<h4>{}</h4>'.format(
                escape(MIGRATOR_TITLES.get(res.name, res.name)))
            html += section('Skipped (already on target with same SID)', res.skipped)
            html += section('Rebound (adopt target SID, push config)', res.rebound)
            html += section('Created on target', res.created)
            html += section('Target-only (imported by SYNC after cutover)',
                            res.warnings)
            html += section(
                'Errors', ['{}: {}'.format(k, v) for k, v in res.errors],
                css='text-danger')
            if not (res.skipped or res.rebound or res.created or res.warnings
                    or res.errors):
                html += '<p>Nothing to migrate.</p>'
        return html

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_preview(self):
        self.ensure_one()
        client = self._get_target_client()
        results = [migrator_cls(self.env).run(client, dry_run=True)
                   for migrator_cls in MIGRATORS]
        self.write({'result': self._render_result(results), 'state': 'preview'})
        return self._reopen()

    def action_migrate(self):
        self.ensure_one()
        client = self._get_target_client()
        results = [migrator_cls(self.env).run(client, dry_run=False)
                   for migrator_cls in MIGRATORS]
        total_errors = sum(len(res.errors) for res in results)
        if total_errors:
            # Cutting credentials over while anything failed to migrate would
            # strand Connect on an account it can't fully talk to and destroy
            # the only copy of the source auth token in the same move. Leave
            # the account untouched so the admin can fix the errors (e.g.
            # transfer a number via the Twilio console, resolve a domain name
            # conflict) and click Migrate again; nothing here is destructive
            # to retry.
            html = self._render_result(results) + (
                '<p class="text-danger"><strong>Migration NOT completed: '
                '{} item(s) failed. Connect is still on the source account. '
                'Resolve the errors above and click Migrate again.'
                '</strong></p>'.format(total_errors))
            self.write({'result': html, 'state': 'preview'})
            return self._reopen()
        self._cutover()
        html = self._render_result(results) + self._render_manual_steps()
        self.write({'result': html, 'state': 'done'})
        return self._reopen()

    def _cutover(self):
        settings = self.env['connect.settings'].sudo()
        data = settings.search([], limit=1)
        if not data:
            data = settings.with_context(no_constrains=True).create({})
        # display_auth_token goes through the protected-fields write path,
        # which stores the real value in auth_token and masks the display.
        # Region token and API key/secret are account-scoped: stale values
        # would fail silently at AccessToken time, so clear them.
        data.write({
            'account_sid': self.target_account_sid,
            'display_auth_token': self.target_auth_token,
            'rest_api_host': (self.target_rest_api_host or '').strip() or False,
            'region_auth_token': False,
            'display_region_auth_token': False,
            'twilio_api_key': False,
            'twilio_api_secret': False,
            'display_twilio_api_secret': False,
        })

    def _render_manual_steps(self):
        callerids = self.env['connect.outgoing_callerid'].search([])
        steps = [
            'Re-verify outgoing caller IDs on the target account (Twilio '
            'only offers an interactive validation flow, they cannot be '
            'migrated): {}.'.format(
                ', '.join(callerids.mapped('number')) if callerids else 'none'),
            'Mint a new API Key SID/Secret on the target account for the '
            'browser phone.',
            'Re-register WhatsApp senders and content templates on the '
            'target account.',
            'Historical call/message logs are not migrated; use Twilio '
            'Bulk Export.',
            'Run SYNC TWILIO ACCOUNT to import target-only resources.',
        ]
        return '<h4>Manual steps</h4><ol>{}</ol>'.format(
            ''.join('<li>{}</li>'.format(escape(k)) for k in steps))
