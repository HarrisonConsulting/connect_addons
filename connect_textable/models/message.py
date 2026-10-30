import hashlib
import logging
import time
from datetime import timedelta

import psycopg2.errors
from markupsafe import Markup
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools.textable_client import (
    ACCEPTED, AMBIGUOUS, MAX_MESSAGE_LENGTH, MAYBE_SENT_OUTCOMES,
    REJECTED_REQUEST, TextableError, TransportResult, is_nanp_e164, last4)
from .number import normalize_phone

_logger = logging.getLogger(__name__)

# A derived send key is bucketed in windows of this many seconds; the
# current and the previous bucket are both checked.
SEND_KEY_WINDOW = 300
# Echoes are matched to sends made at most this long ago.
ECHO_WINDOW = timedelta(minutes=15)
CLAIM_LOCK_TIMEOUT = '10s'
# First keys of the two-key advisory locks: per sender number for send
# claims, per MessageID for relay posts.
CLAIM_LOCK_CLASS = 0x54584254
RELAY_LOCK_CLASS = 0x54584252

# Textable's own opt-out keywords; START opts back in.
STOP_KEYWORDS = frozenset({'STOP', 'END', 'CANCEL', 'QUIT', 'UNSUBSCRIBE', 'OPTOUT'})
START_KEYWORDS = frozenset({'START'})
HELP_KEYWORDS = frozenset({'HELP'})

MAX_RELAY_FIELD_LENGTH = 128
MAX_RELAY_BODY_LENGTH = 4096

TEXTABLE_STATES = [
    ('sent_unconfirmed', 'Sent, delivery unconfirmed'),
    ('unknown', 'Outcome unknown, not resent'),
    ('failed', 'Failed'),
    ('received', 'Received'),
    ('foreign', 'Sent outside Odoo'),
]
STATE_LABELS = dict(TEXTABLE_STATES)

OUTCOME_TEXT = {
    ACCEPTED: 'Textable accepted the message. Delivery is not confirmed.',
    AMBIGUOUS: 'Textable may have taken the message. It is not sent again; '
               'the relay echo settles it.',
    'rejected_request': 'Textable refused the request.',
    'rejected_contact': 'Textable refused the sender user or the contact.',
    'auth_failed': 'Textable refused the sender number\'s token.',
    'throttled': 'Textable is throttling this sender; nothing was sent.',
    'unreachable': 'Textable could not be reached; nothing was sent.',
}

# Logged once per process for each Textable user whose relay posts carry
# an AccessToken. The value itself is never logged or stored.
_ACCESS_TOKEN_WARNED = set()


class TextableGateError(UserError):
    """A send the local gate refuses. Nothing was sent."""

    def __init__(self, code, message):
        self.textable_code = code
        super().__init__('Textable send refused (%s): %s' % (code, message))


def _sha256(text):
    return hashlib.sha256((text or '').encode()).hexdigest()


def _prepare_claim_cursor(cr, number_id):
    """Serialize claims per sender number.

    Read committed, so the counts taken after the lock see the intents that
    the previous holder committed; Odoo cursors default to repeatable read,
    whose snapshot would predate the lock. Both settings only apply to a
    transaction that has not started: a test cursor, which runs inside the
    test's own transaction, takes the lock and changes nothing else.
    """
    if cr.connection.get_transaction_status() == TRANSACTION_STATUS_IDLE:
        cr.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        cr.execute("SET LOCAL lock_timeout = '%s'" % CLAIM_LOCK_TIMEOUT)
    cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', (CLAIM_LOCK_CLASS, number_id))


def _relay_text(payload, key, limit=MAX_RELAY_FIELD_LENGTH):
    """A string field of the relay payload, stripped; None when absent or
    not a string within limit."""
    value = payload.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and key.endswith('ID'):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > limit:
        return None
    return value


class ConnectMessage(models.Model):
    _inherit = 'connect.message'

    textable_number_id = fields.Many2one(
        'connect.textable.number', string='Textable Number', readonly=True,
        ondelete='restrict', index=True,
        help='The owned Textable number this message was sent from or to.')
    textable_state = fields.Selection(
        TEXTABLE_STATES, string='Textable State', readonly=True, index=True,
        help='What is actually known. Textable reports no delivery, so a sent '
             'message stays "delivery unconfirmed"; an unknown outcome is '
             'never sent again.')
    textable_send_key = fields.Char(
        readonly=True, index=True,
        help='Key of the send intent this message records.')
    textable_body_hash = fields.Char(
        readonly=True,
        help='sha256 of the body, used to match relay echoes.')
    textable_conversation_id = fields.Char(
        string='Textable Conversation', readonly=True,
        help='Textable ConversationID (its contact id) from the relay.')
    textable_keyword = fields.Char(
        readonly=True,
        help='STOP, START or HELP keyword recognized in an inbound message.')

    _textable_message_uid_unique = models.UniqueIndex(
        '(message_sid) WHERE textable_number_id IS NOT NULL AND message_sid IS NOT NULL',
        'This Textable MessageID is already recorded.',
    )

    @api.model
    def _own_numbers(self):
        """Textable numbers this database sends from."""
        return super()._own_numbers() | set(
            self.env['connect.textable.number'].sudo().with_context(
                active_test=False).search([]).mapped('phone_number'))

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def send(self, recipient, body, res_id=None, res_model=None,
             outgoing_callerid=None, **kwargs):
        if self.env['connect.settings']._get_message_provider() != 'textable':
            return super().send(
                recipient, body, res_id=res_id, res_model=res_model,
                outgoing_callerid=outgoing_callerid, **kwargs)
        return self._textable_send(
            recipient, body, res_id=res_id, res_model=res_model,
            sender=outgoing_callerid, idempotency_key=kwargs.get('idempotency_key'))

    @api.model
    def _textable_send(self, recipient, body, res_id=None, res_model=None,
                       sender=None, idempotency_key=None, allow_unassigned=False):
        """Send one SMS through the sender number's own Textable user.

        Transport failures are recorded and returned, not raised, so the
        message keeps its honest state; a refusal by the gate raises
        TextableGateError and sends nothing. ``allow_unassigned`` is for
        server-side callers that already checked the user (the test-message
        wizard); RPC cannot reach this method.
        """
        user = self.env.user
        privileged = bool(self.env.su or user._is_superuser() or allow_unassigned)
        recipient = normalize_phone(recipient or '')
        body = body or ''
        if res_model:
            if res_model not in self.env:
                raise ValidationError('Unknown model %s.' % res_model)
            if res_id:
                # As the calling user, before any sudo below.
                self.env[res_model].browse(res_id).check_access('read')
        number = self._textable_resolve_sender(sender, user, privileged)
        # Built before the claim: a configuration error leaves no intent.
        client = number._textable_client()
        body_hash = _sha256(body)
        keys = self._textable_send_keys(
            number, recipient, body_hash, res_model, res_id, user, idempotency_key)
        intent_id, send_key, claimed, prior_outcome = self._textable_claim(
            number, recipient, body, body_hash, keys, user, privileged)
        if not claimed:
            return self._textable_record_duplicate(
                number, recipient, body, body_hash, send_key, prior_outcome,
                user, res_model, res_id)
        try:
            result = client.send(number.phone_number, recipient, body)
        except TextableError as exc:
            # Refused before any request was made.
            result = TransportResult(REJECTED_REQUEST, None, str(exc))
        self._textable_record_outcome(intent_id, result)
        _logger.info(
            'Textable send %s from number %s to %s: %s (%s)',
            intent_id, number.id, last4(recipient), result.outcome, result.detail)
        message = self._textable_create_outbound(
            number, recipient, body, body_hash, send_key, result.outcome, user,
            res_model, res_id)
        self._textable_after_send(message, result.outcome, user, res_model, res_id)
        return message

    @api.model
    def _textable_resolve_sender(self, sender, user, privileged):
        """The number to send from: the named one, else the user's first
        assigned number. Whether the user may use it is the gate's call."""
        Number = self.env['connect.textable.number'].sudo()
        domain = [('active', '=', True)]
        if not privileged:
            domain.append(('company_id', 'in', user.company_ids.ids))
        by_assignment = False
        if sender:
            domain.append(('phone_number', '=', normalize_phone(sender)))
        else:
            connect_user = self.env['connect.user'].sudo().search(
                [('user', '=', user.id)], limit=1)
            if connect_user:
                domain.append(('user_ids', 'in', connect_user.ids))
                by_assignment = True
            elif not privileged:
                raise TextableGateError(
                    'sender_not_assigned', 'you have no Textable number assigned.')
        candidates = Number.search(domain)
        if not candidates:
            raise TextableGateError(
                'no_sender', 'no active Textable number matches the sender.')
        preferred = candidates.filtered(
            lambda n: n.company_id == self.env.company) or candidates
        if not (sender or by_assignment) and len(preferred) > 1:
            raise TextableGateError(
                'no_sender', 'several Textable numbers exist; name the sender.')
        return preferred[:1]

    @api.model
    def _textable_send_keys(self, number, recipient, body_hash, res_model,
                            res_id, user, idempotency_key):
        """The key to claim, then the keys a duplicate may sit under.

        A caller's key is namespaced by sender number and user. Without
        one, the key covers sender, recipient, body, target record and user
        in five-minute buckets; the previous bucket is checked too.
        """
        if idempotency_key:
            return [_sha256('caller|%s|%s|%s' % (number.id, user.id, idempotency_key))]
        bucket = int(time.time()) // SEND_KEY_WINDOW
        base = 'derived|%s|%s|%s|%s|%s|%s' % (
            number.id, recipient, body_hash, res_model or '', res_id or 0, user.id)
        return [_sha256('%s|%s' % (base, b)) for b in (bucket, bucket - 1)]

    @api.model
    def _textable_claim(self, number, recipient, body, body_hash, keys, user, privileged):
        """Write the intent through its own cursor and commit it before the
        POST. Returns (intent id, its send key, claimed, prior outcome)."""
        try:
            with self.env.registry.cursor() as cr:
                _prepare_claim_cursor(cr, number.id)
                claim_env = self.env(cr=cr, su=True)
                Intent = claim_env['connect.textable.send']
                existing = Intent.search([('send_key', 'in', keys)], order='id desc', limit=1)
                if existing and (existing.state == 'queued'
                                 or existing.outcome in MAYBE_SENT_OUTCOMES):
                    _logger.info(
                        'Textable send intent %s exists (%s); not sent again.',
                        existing.id, existing.outcome or 'in flight')
                    return existing.id, existing.send_key, False, existing.outcome or False
                self.with_env(claim_env)._textable_check_send_allowed(
                    claim_env['connect.textable.number'].browse(number.id),
                    recipient, body, user, privileged)
                if existing:
                    # A definite failure under the same key: post again.
                    existing.write({
                        'state': 'queued', 'outcome': False, 'http_status': 0,
                        'detail': False, 'finished_at': False,
                        'attempts': existing.attempts + 1, 'user_id': user.id,
                    })
                    return existing.id, existing.send_key, True, False
                intent = Intent.create({
                    'send_key': keys[0],
                    'number_id': number.id,
                    'company_id': number.company_id.id,
                    'from_number': number.phone_number,
                    'to_number': recipient,
                    'body_hash': body_hash,
                    'user_id': user.id,
                })
                return intent.id, intent.send_key, True, False
        except psycopg2.errors.LockNotAvailable as exc:
            raise UserError(
                'The Textable number %s is busy; nothing was sent. Try again.'
                % number.name) from exc

    def _textable_check_send_allowed(self, number, recipient, body, user, privileged):
        """The local Textable send gate, in one place.

        These checks move to Connect core with 6642 (the two-tier gate,
        D7); until then they live here and nowhere else. Runs in the claim
        cursor under the per-number lock, so the caps count every intent
        committed before it. Raises TextableGateError.
        """
        # Assigned sender, unless the caller is the system.
        if not privileged:
            connect_user = self.env['connect.user'].sudo().search(
                [('user', '=', user.id)], limit=1)
            if (number.company_id not in user.company_ids
                    or not connect_user or connect_user not in number.user_ids):
                raise TextableGateError(
                    'sender_not_assigned',
                    'you are not assigned to the Textable number %s.' % number.name)
        if not number.active:
            raise TextableGateError(
                'sender_inactive', 'the Textable number %s is archived.' % number.name)
        if number.campaign_state != 'approved':
            raise TextableGateError(
                'campaign_not_approved',
                'the 10DLC campaign of %s is not approved.' % number.name)
        if not is_nanp_e164(recipient):
            raise TextableGateError(
                'destination_not_nanp',
                'the recipient %s is not a NANP number in E.164 form.' % last4(recipient))
        if not body.strip() or len(body) > MAX_MESSAGE_LENGTH:
            raise TextableGateError(
                'invalid_body',
                'the message must have 1 to %d characters.' % MAX_MESSAGE_LENGTH)
        if self.env['phone.blacklist'].sudo().search_count(
                [('number', '=', recipient)], limit=1):
            raise TextableGateError(
                'recipient_opted_out',
                'the recipient %s has opted out.' % last4(recipient))
        Intent = self.env['connect.textable.send'].sudo()
        now = fields.Datetime.now()
        maybe_sent = ['|', ('outcome', '=', False), ('outcome', 'in', sorted(MAYBE_SENT_OUTCOMES))]
        sender_domain = [('number_id', '=', number.id)] + maybe_sent
        if Intent.search_count(sender_domain + [
                ('create_date', '>=', now - timedelta(hours=1))]) >= number.hourly_cap:
            raise TextableGateError(
                'sender_hourly_cap',
                '%s reached its hourly cap of %d.' % (number.name, number.hourly_cap))
        if Intent.search_count(sender_domain + [
                ('create_date', '>=', now - timedelta(days=1))]) >= number.daily_cap:
            raise TextableGateError(
                'sender_daily_cap',
                '%s reached its daily cap of %d.' % (number.name, number.daily_cap))
        if Intent.search_count([
                ('company_id', '=', number.company_id.id),
                ('to_number', '=', recipient),
                ('create_date', '>=', now - timedelta(days=1))] + maybe_sent
                ) >= number.recipient_daily_cap:
            raise TextableGateError(
                'recipient_daily_cap',
                'the recipient %s reached the daily cap of %d.'
                % (last4(recipient), number.recipient_daily_cap))

    @api.model
    def _textable_record_outcome(self, intent_id, result):
        """Record the outcome through its own cursor. When that fails the
        intent stays in flight, which is the safe side: it is never sent
        again."""
        try:
            with self.env.registry.cursor() as cr:
                self.env(cr=cr, su=True)['connect.textable.send'].browse(intent_id).write({
                    'state': 'done',
                    'outcome': result.outcome,
                    'http_status': result.http_status or 0,
                    'detail': result.detail,
                    'finished_at': fields.Datetime.now(),
                })
        except Exception:  # noqa: BLE001 - the send already happened
            _logger.exception(
                'Textable send intent %s: outcome %s not recorded; the intent '
                'stays in flight and is never sent again.', intent_id, result.outcome)

    @api.model
    def _textable_outbound_values(self, number, recipient, body, body_hash,
                                  send_key, outcome, user, res_model, res_id):
        if outcome == ACCEPTED:
            status, state = 'sent', 'sent_unconfirmed'
        elif outcome in (AMBIGUOUS, False, None):
            status, state = 'unknown', 'unknown'
        else:
            status, state = 'failed', 'failed'
        partner = self._textable_find_partner(recipient, number.company_id)
        return {
            'message_type': 'sms',
            'from_number': number.phone_number,
            'to_number': recipient,
            'body': body,
            'sender_user': user.id,
            'partner': partner.id,
            'res_model': res_model or False,
            'res_id': res_id or False,
            'status': status,
            'textable_state': state,
            'textable_number_id': number.id,
            'textable_send_key': send_key,
            'textable_body_hash': body_hash,
            'has_error': outcome != ACCEPTED,
            'error_code': (outcome or AMBIGUOUS) if outcome != ACCEPTED else False,
            'error_message': OUTCOME_TEXT.get(outcome or AMBIGUOUS) if outcome != ACCEPTED else False,
        }

    @api.model
    def _textable_create_outbound(self, number, recipient, body, body_hash,
                                  send_key, outcome, user, res_model, res_id):
        return self.sudo().create(self._textable_outbound_values(
            number, recipient, body, body_hash, send_key, outcome, user,
            res_model, res_id))

    @api.model
    def _textable_record_duplicate(self, number, recipient, body, body_hash,
                                   send_key, prior_outcome, user, res_model, res_id):
        """A send under an existing intent posts nothing. Return the message
        recorded for it, or record one with the intent's honest state when
        the original transaction never committed its own."""
        existing = self.sudo().search(
            [('textable_send_key', '=', send_key)], order='id desc', limit=1)
        if existing:
            return existing
        message = self._textable_create_outbound(
            number, recipient, body, body_hash, send_key, prior_outcome, user,
            res_model, res_id)
        message.error_message = (
            'A send with the same key was already made; nothing was sent '
            'again. %s' % OUTCOME_TEXT.get(prior_outcome or AMBIGUOUS))
        return message

    @api.model
    def _textable_after_send(self, message, outcome, user, res_model, res_id):
        state_label = STATE_LABELS[message.textable_state]
        if outcome != ACCEPTED and not user._is_superuser():
            self.env['connect.settings'].connect_notify(
                '%s: %s' % (state_label, OUTCOME_TEXT.get(outcome, '')),
                title='Textable', notify_uid=user.id, sticky=True, warning=True)
        if not (res_model and res_id):
            return
        record = self.env[res_model].sudo().browse(res_id)
        if not (record.exists() and hasattr(record, 'message_post')):
            return
        note = Markup('{}<br/><small>SMS: {} · <a href="/odoo/connect.message/{}">'
                      'Message</a></small>').format(message.body or '', state_label, message.id)
        record.with_context(mail_create_nosubscribe=True).message_post(
            body=note, subtype_xmlid='mail.mt_note', message_type='comment',
            author_id=user.partner_id.id)
        self.env['connect.settings'].connect_reload_view(res_model)

    @api.model
    def _textable_find_partner(self, phone, company):
        if not phone:
            return self.env['res.partner']
        return self.env['res.partner'].sudo().search(
            [('phone_sanitized', '=', phone), ('company_id', 'in', [False, company.id])],
            limit=1)

    # ------------------------------------------------------------------
    # Relay webhook
    # ------------------------------------------------------------------

    @api.model
    def _textable_relay(self, payload, relay_user):
        """Handle one relay post whose path token matched the relay secret
        of the Textable user ``relay_user``.

        Returns 'ok', 'duplicate', 'dropped' (not ours, nothing written),
        'refused' (organization or user mismatch) or 'invalid'.
        """
        if not isinstance(payload, dict):
            return 'invalid'
        expected_org = self.env['connect.settings']._textable_organization_id()
        organization = _relay_text(payload, 'TextableOrganizationID')
        if not expected_org:
            _logger.critical(
                'SECURITY: Textable relay post refused: no Textable organization '
                'id is configured.')
            return 'refused'
        if organization != expected_org:
            _logger.critical(
                'SECURITY: Textable relay post refused: TextableOrganizationID '
                'does not match.')
            return 'refused'
        # Textable user ids are not secret: what binds a post to a user is
        # the relay token, so the payload must name the token's own user.
        textable_user = _relay_text(payload, 'TextableUserID')
        if not relay_user or textable_user != relay_user:
            _logger.critical(
                'SECURITY: Textable relay post refused: TextableUserID is not the '
                'Textable user this relay URL was issued to.')
            return 'refused'
        Number = self.env['connect.textable.number'].sudo()
        direction = payload.get('MessageDirection')
        message_uid = _relay_text(payload, 'MessageID')
        to_number = normalize_phone(_relay_text(payload, 'ToNumber', 32) or '')
        from_number = normalize_phone(_relay_text(payload, 'FromNumber', 32) or '')
        body = payload.get('MessageBody') or ''
        media = payload.get('Media') or []
        if (direction not in ('in', 'out') or not message_uid
                or not to_number or not from_number
                or not isinstance(body, str) or len(body) > MAX_RELAY_BODY_LENGTH
                or not isinstance(media, list)):
            _logger.warning('Textable relay post refused: malformed payload.')
            return 'invalid'
        owned = to_number if direction == 'in' else from_number
        number = Number.search([
            ('phone_number', '=', owned),
            ('textable_user_id', '=', textable_user),
            ('active', '=', True),
        ])
        if len(number) != 1:
            _logger.info(
                'Textable relay post dropped: %s message for %s, which is not a '
                'number this Textable user holds here.', direction, last4(owned))
            return 'dropped'
        if payload.get('AccessToken') and textable_user not in _ACCESS_TOKEN_WARNED:
            _ACCESS_TOKEN_WARNED.add(textable_user)
            _logger.warning(
                'Textable relay posts for Textable user %s carry an AccessToken. '
                'It is ignored and never stored; consider whether that user\'s '
                'token should be rotated.', textable_user)
        if self._textable_lock_message_uid(message_uid):
            return 'duplicate'
        conversation = _relay_text(payload, 'ConversationID')
        if direction == 'in':
            self._receive_textable(
                number, message_uid, from_number, to_number, body,
                conversation_id=conversation, media_count=len(media))
        else:
            self._textable_reconcile_echo(
                number, message_uid, from_number, to_number, body,
                conversation_id=conversation)
        return 'ok'

    @api.model
    def _textable_lock_message_uid(self, message_uid):
        """Serialize relay posts carrying the same MessageID until this
        transaction ends, then report whether it is already recorded.

        The lookup runs on a fresh cursor: this request's snapshot predates
        the lock and would miss a copy committed while it waited. Losing
        that race would surface as a unique violation, whose failed INSERT
        Odoo logs with every value in it.
        """
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(%s, hashtext(%s))',
            (RELAY_LOCK_CLASS, message_uid))
        with self.env.registry.cursor() as cr:
            cr.execute(
                'SELECT 1 FROM connect_message WHERE textable_number_id IS NOT NULL '
                'AND message_sid = %s UNION ALL SELECT 1 FROM connect_textable_send '
                'WHERE echo_message_uid = %s LIMIT 1', (message_uid, message_uid))
            return bool(cr.fetchone())

    @api.model
    def _textable_create_relay_message(self, vals):
        """Create, or return an empty recordset when a copy with the same
        MessageID got in anyway (the UniqueIndex is the backstop)."""
        try:
            with self.env.cr.savepoint():
                return self.sudo().create(vals)
        except psycopg2.errors.UniqueViolation:
            return self.browse()

    @api.model
    def _receive_textable(self, number, message_uid, from_number, to_number,
                          body, conversation_id=None, media_count=0):
        """Record an inbound message to an owned number.

        Media is counted, not fetched. Nothing is created from the message
        but the message itself; it is threaded to the last message sent to
        that contact and posted on that message's record.
        """
        Message = self.sudo()
        last_out = Message.search([
            ('textable_number_id', '=', number.id),
            ('from_number', '=', to_number),
            ('to_number', '=', from_number),
            ('direction', '=', 'outgoing'),
        ], order='id desc', limit=1)
        partner = self._textable_find_partner(from_number, number.company_id)
        keyword = self._textable_keyword(body)
        vals = {
            'message_type': 'mms' if media_count else 'sms',
            'num_media': media_count,
            'from_number': from_number,
            'to_number': to_number,
            'body': body,
            'status': 'received',
            'message_sid': message_uid,
            'partner': partner.id,
            'parent_message': last_out.id,
            'textable_state': 'received',
            'textable_number_id': number.id,
            'textable_conversation_id': conversation_id,
            'textable_keyword': keyword or False,
        }
        if (last_out.res_model and last_out.res_id and last_out.res_model in self.env
                and self.env[last_out.res_model].sudo().browse(last_out.res_id).exists()):
            vals.update({'res_model': last_out.res_model, 'res_id': last_out.res_id})
        message = self._textable_create_relay_message(vals)
        if not message:
            return message
        _logger.info(
            'Textable message %s received on number %s from %s',
            message.id, number.id, last4(from_number))
        if keyword:
            self._textable_apply_keyword(number, from_number, keyword, message)
        self._textable_post_inbound(message, partner)
        return message

    @staticmethod
    def _textable_keyword(body):
        word = (body or '').strip().upper().rstrip('.!')
        if word in STOP_KEYWORDS | START_KEYWORDS | HELP_KEYWORDS:
            return word
        return None

    @api.model
    def _textable_apply_keyword(self, number, phone, keyword, message):
        """STOP and START change phone.blacklist, only for an owned number
        and within its hourly cap. START lifts only an entry a STOP through
        this module made. HELP changes nothing: Textable answers it."""
        if keyword in HELP_KEYWORDS:
            return
        action = 'stop' if keyword in STOP_KEYWORDS else 'start'
        Optout = self.env['connect.textable.optout'].sudo()
        vals = {
            'phone': phone,
            'number_id': number.id,
            'company_id': number.company_id.id,
            'action': action,
            'keyword': keyword,
            'message_id': message.id,
        }
        used = Optout.search_count([
            ('number_id', '=', number.id),
            ('capped', '=', False),
            ('create_date', '>=', fields.Datetime.now() - timedelta(hours=1)),
        ])
        if used >= number.optout_hourly_cap:
            _logger.warning(
                'Textable opt-out cap of number %s reached: %s from %s did not '
                'change the blacklist.', number.id, keyword, last4(phone))
            Optout.create(dict(vals, capped=True))
            return
        Blacklist = self.env['phone.blacklist'].sudo()
        note = Markup('{} received on Textable number {}.').format(keyword, number.name)
        try:
            with self.env.cr.savepoint():
                if action == 'stop':
                    already = Blacklist.search([('number', '=', phone)], limit=1)
                    if not already:
                        Blacklist._add([phone], message=note)
                    Optout.create(dict(vals, created_entry=not already))
                else:
                    made = Optout.search([
                        ('phone', '=', phone), ('action', '=', 'stop'),
                        ('created_entry', '=', True), ('released', '=', False)])
                    if made:
                        Blacklist._remove([phone], message=note)
                        made.write({'released': True})
                    Optout.create(vals)
        except UserError:
            # phone.blacklist could not format the number.
            _logger.warning(
                'Textable %s from %s not applied: the number cannot be formatted.',
                keyword, last4(phone))
            return
        _logger.info(
            'Textable %s from %s applied for number %s', keyword, last4(phone), number.id)

    @api.model
    def _textable_post_inbound(self, message, partner):
        if not (message.res_model and message.res_id):
            return
        record = self.env[message.res_model].sudo().browse(message.res_id)
        if not (record.exists() and hasattr(record, 'message_post')):
            return
        # The body is sender-controlled: Markup.format escapes it.
        body = Markup('{}<br/><small>SMS received · <a href="/odoo/connect.message/{}">'
                      'Message</a></small>').format(message.body or '', message.id)
        kwargs = {'body': body, 'subtype_xmlid': 'mail.mt_comment', 'message_type': 'comment'}
        if partner:
            kwargs['author_id'] = partner.id
        record.with_context(mail_create_nosubscribe=True).message_post(**kwargs)
        self.env['connect.settings'].connect_reload_view(message.res_model)

    @api.model
    def _textable_reconcile_echo(self, number, message_uid, from_number,
                                 to_number, body, conversation_id=None):
        """An 'out' relay post: Textable sent a message from an owned number.

        A unique unconfirmed or unknown send with the same sender, recipient
        and body in the window takes the echo's MessageID; an unknown one
        becomes sent, delivery unconfirmed. A unique in-flight intent whose
        message is not committed yet takes the MessageID on the intent.
        Anything else, a send made in the Textable app included, is
        journaled as a foreign outbound message.
        """
        body_hash = _sha256(body)
        now = fields.Datetime.now()
        since = now - ECHO_WINDOW
        Message = self.sudo()
        Intent = self.env['connect.textable.send'].sudo()
        candidates = Message.search([
            ('textable_number_id', '=', number.id),
            ('from_number', '=', from_number),
            ('to_number', '=', to_number),
            ('textable_body_hash', '=', body_hash),
            ('textable_state', 'in', ('sent_unconfirmed', 'unknown')),
            ('message_sid', '=', False),
            ('create_date', '>=', since),
        ], limit=2)
        if len(candidates) == 1:
            vals = {'message_sid': message_uid, 'textable_conversation_id': conversation_id}
            if candidates.textable_state == 'unknown':
                vals.update({
                    'status': 'sent', 'textable_state': 'sent_unconfirmed',
                    'has_error': False, 'error_code': False, 'error_message': False,
                })
            try:
                with self.env.cr.savepoint():
                    candidates.write(vals)
            except psycopg2.errors.UniqueViolation:
                return candidates.browse()
            Intent.search([('send_key', '=', candidates.textable_send_key)]).write({
                'echo_message_uid': message_uid, 'echo_received_at': now})
            _logger.info('Textable echo reconciled message %s', candidates.id)
            return candidates
        if not candidates:
            intents = Intent.search([
                ('number_id', '=', number.id),
                ('from_number', '=', from_number),
                ('to_number', '=', to_number),
                ('body_hash', '=', body_hash),
                ('echo_message_uid', '=', False),
                ('create_date', '>=', since),
                '|', ('outcome', '=', False), ('outcome', 'in', sorted(MAYBE_SENT_OUTCOMES)),
            ], limit=2)
            if len(intents) == 1:
                intents.write({'echo_message_uid': message_uid, 'echo_received_at': now})
                _logger.info('Textable echo matched send intent %s', intents.id)
                return Message.browse()
        else:
            _logger.warning(
                'Textable echo from number %s matches several unconfirmed sends; '
                'none is reconciled and the echo is journaled as foreign.', number.id)
        message = self._textable_create_relay_message({
            'message_type': 'sms',
            'from_number': from_number,
            'to_number': to_number,
            'body': body,
            'status': 'sent',
            'message_sid': message_uid,
            'partner': self._textable_find_partner(to_number, number.company_id).id,
            'textable_state': 'foreign',
            'textable_number_id': number.id,
            'textable_body_hash': body_hash,
            'textable_conversation_id': conversation_id,
        })
        if message:
            _logger.info('Textable outbound message %s journaled as foreign', message.id)
        return message
