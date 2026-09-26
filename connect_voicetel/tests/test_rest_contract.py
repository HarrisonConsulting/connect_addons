# -*- coding: utf-8 -*-
"""REST contract test for connect_voicetel, against a mocked transport.

Spins up a mock VoiceTel REST server, points the voiceml SDK at it, and
exercises every REST call connect_voicetel's OWN code makes — the calls
built in models/settings.py (originate), models/application.py, models/
domain.py, models/user.py (SIP credentials), models/number.py, models/
outgoing_callerid.py (the raw httpx client) — asserting the exact HTTP
method, path (with the .json suffix and SIP sub-paths), Basic auth, and the
form-encoded field names. No live VoiceTel account, no Odoo database.

This is the connect_voicetel port of the standalone script the source
branch shipped as tools/rest_contract.py: a runnable script is not a test a
gate can run, so this is a unittest.TestCase colocated with the module
instead, scoped to exactly the calls this module's ported code makes (it
drops the source script's routes_v2 assertions — nothing in this module
calls client.routes_v2).
"""

import base64
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

try:
    from voiceml import Client
    from voiceml.models import CreateApplicationRequest, CreateCallRequest
    VOICEML_AVAILABLE = True
except ImportError:
    VOICEML_AVAILABLE = False

ACCOUNT_SID = 'AC_TEST_ACCOUNT_SID'
API_KEY = 'test-api-key'

SIDS = {
    'domain': 'SD-domain-1',
    'cred_list': 'CL-list-1',
    'credential': 'CR-cred-1',
    'ocid': 'OC-ocid-1',
}


class _Handler(BaseHTTPRequestHandler):
    requests = []

    def _handle(self, method):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length).decode('utf-8') if length else ''
        parsed = urllib.parse.urlsplit(self.path)
        _Handler.requests.append({
            'method': method,
            'path': parsed.path,
            'auth': self.headers.get('Authorization'),
            'body': body,
        })
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{}')

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')

    def do_PUT(self):
        self._handle('PUT')

    def do_DELETE(self):
        self._handle('DELETE')

    def log_message(self, *args):
        pass


def _call(fn):
    """Run an SDK call, tolerating response-validation errors.

    The mock returns {} for every response, so the SDK's own response
    parsing may raise after the request has already gone out. The contract
    under test is the REQUEST, so the response body is irrelevant.
    """
    try:
        fn()
    except Exception:
        pass


def _parse_form(body):
    return urllib.parse.parse_qs(body)


@unittest.skipUnless(VOICEML_AVAILABLE, 'voiceml package not importable in this environment')
class TestVoicetelRestContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _Handler.requests = []
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = 'http://127.0.0.1:{}'.format(cls.server.server_address[1])
        cls.client = Client(account_sid=ACCOUNT_SID, api_key=API_KEY, base_url=cls.base_url)
        cls.root = '/2010-04-01/Accounts/{}'.format(ACCOUNT_SID)
        cls.expected_auth = 'Basic ' + base64.b64encode(
            '{}:{}'.format(ACCOUNT_SID, API_KEY).encode()).decode()
        cls._exercise()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        super().tearDownClass()

    @classmethod
    def _exercise(cls):
        client = cls.client

        # models/settings.py: originate_call()
        _call(lambda: client.calls.create(CreateCallRequest(
            to='sip:1001@example.com',
            from_='+18005551234',
            twiml='<Response><Dial><Number>+18005559999</Number></Dial></Response>',
            status_callback='https://odoo.example.com/voicetel/webhook/callstatus',
            status_callback_event=['initiated', 'answered', 'completed'],
            record=True,
            recording_channels='dual',
            recording_status_callback='https://odoo.example.com/voicetel/webhook/recordingstatus',
            recording_status_callback_event='completed',
        )))

        # models/message.py: send()
        _call(lambda: client.messages.create(
            to='+18005559999', body='hello', from_='+18005551234'))

        # models/application.py: create_application() / sync()
        _call(lambda: client.applications.create(CreateApplicationRequest(
            friendly_name='VoiceTel App',
            voice_url='https://odoo.example.com/voicetel/webhook/application/1',
            status_callback='https://odoo.example.com/voicetel/webhook/callstatus',
        )))
        _call(lambda: client.applications.list())

        # models/number.py: sync() / update_number()
        _call(lambda: client.incoming_phone_numbers.list())
        _call(lambda: client.incoming_phone_numbers.update(
            'PN-number-1', friendly_name='Main',
            voice_url='https://odoo.example.com/voicetel/webhook/number'))

        # models/recording.py: on_recording_status_voicetel() / sync_voicetel()
        _call(lambda: client.recordings.get('RE-rec-1'))

        # models/domain.py: create_domain() / sync() / unlink()
        _call(lambda: client.sip.domains.create(
            domain_name='test.example.com',
            friendly_name='Test Domain',
            voice_url='https://odoo.example.com/voicetel/webhook/domain',
            voice_method='POST',
            sip_registration=True,
        ))
        _call(lambda: client.sip.domains.list())
        _call(lambda: client.sip.credential_lists.create(friendly_name='Test Domain'))
        _call(lambda: client.sip.domains.auth.registrations.credential_list_mappings(
            SIDS['domain']).create(credential_list_sid=SIDS['cred_list']))
        _call(lambda: client.sip.domains.auth.calls.credential_list_mappings(
            SIDS['domain']).create(credential_list_sid=SIDS['cred_list']))

        # models/user.py: _create_sip_account() / _update_sip_password() /
        # delete_sip_account() / _get_voicetel_client_token()
        _call(lambda: client.sip.credential_lists.credentials(SIDS['cred_list']).create(
            username='1001', password='s3cret-pass'))
        _call(lambda: client.sip.credential_lists.credentials(SIDS['cred_list']).update(
            SIDS['credential'], password='new-pass'))
        _call(lambda: client.sip.credential_lists.credentials(SIDS['cred_list']).delete(
            SIDS['credential']))
        _call(lambda: client.sip.credential_lists.delete(SIDS['cred_list']))
        _call(lambda: client.sip.domains.delete(SIDS['domain']))

        # models/outgoing_callerid.py: the raw httpx client (SDK gap)
        raw = httpx.Client(auth=(ACCOUNT_SID, API_KEY), base_url=cls.base_url, timeout=30)
        _call(lambda: raw.get(cls.root + '/OutgoingCallerIds.json'))
        _call(lambda: raw.post(cls.root + '/OutgoingCallerIds.json',
                                data={'PhoneNumber': '+18005551234', 'FriendlyName': 'Office'}))
        _call(lambda: raw.post(cls.root + '/OutgoingCallerIds/{}.json'.format(SIDS['ocid']),
                                data={'FriendlyName': 'Office'}))
        _call(lambda: raw.delete(cls.root + '/OutgoingCallerIds/{}.json'.format(SIDS['ocid'])))
        raw.close()

    def _find(self, path, method=None):
        return next((r for r in _Handler.requests if r['path'] == path
                     and (method is None or r['method'] == method)), None)

    def test_every_request_used_basic_auth(self):
        for req in _Handler.requests:
            self.assertEqual(req['auth'], self.expected_auth, req)

    def test_calls_create(self):
        req = self._find(self.root + '/Calls.json', 'POST')
        self.assertIsNotNone(req)
        body = _parse_form(req['body'])
        for key in ('To', 'From', 'Twiml', 'StatusCallback', 'StatusCallbackEvent',
                    'Record', 'RecordingChannels', 'RecordingStatusCallback',
                    'RecordingStatusCallbackEvent'):
            self.assertIn(key, body)

    def test_messages_create(self):
        req = self._find(self.root + '/Messages.json', 'POST')
        self.assertIsNotNone(req)
        body = _parse_form(req['body'])
        for key in ('To', 'From', 'Body'):
            self.assertIn(key, body)

    def test_applications_create_and_list(self):
        self.assertIsNotNone(self._find(self.root + '/Applications.json', 'POST'))
        self.assertIsNotNone(self._find(self.root + '/Applications.json', 'GET'))

    def test_numbers_list_and_update(self):
        self.assertIsNotNone(self._find(self.root + '/IncomingPhoneNumbers.json', 'GET'))
        self.assertIsNotNone(self._find(
            self.root + '/IncomingPhoneNumbers/PN-number-1.json', 'POST'))

    def test_recording_get(self):
        self.assertIsNotNone(self._find(self.root + '/Recordings/RE-rec-1.json', 'GET'))

    def test_sip_domain_provisioning(self):
        req = self._find(self.root + '/SIP/Domains.json', 'POST')
        self.assertIsNotNone(req)
        body = _parse_form(req['body'])
        for key in ('DomainName', 'VoiceUrl', 'VoiceMethod', 'SipRegistration'):
            self.assertIn(key, body)
        self.assertIsNotNone(self._find(self.root + '/SIP/Domains.json', 'GET'))
        self.assertIsNotNone(self._find(self.root + '/SIP/CredentialLists.json', 'POST'))
        mapping_body = _parse_form(self._find(
            self.root + '/SIP/Domains/{}/Auth/Registrations/CredentialListMappings.json'.format(
                SIDS['domain']), 'POST')['body'])
        self.assertIn('CredentialListSid', mapping_body)
        self.assertIsNotNone(self._find(
            self.root + '/SIP/Domains/{}/Auth/Calls/CredentialListMappings.json'.format(
                SIDS['domain']), 'POST'))
        self.assertIsNotNone(self._find(self.root + '/SIP/Domains/{}.json'.format(
            SIDS['domain']), 'DELETE'))
        self.assertIsNotNone(self._find(self.root + '/SIP/CredentialLists/{}.json'.format(
            SIDS['cred_list']), 'DELETE'))

    def test_sip_credentials(self):
        cred_body = _parse_form(self._find(
            self.root + '/SIP/CredentialLists/{}/Credentials.json'.format(
                SIDS['cred_list']), 'POST')['body'])
        for key in ('Username', 'Password'):
            self.assertIn(key, cred_body)
        self.assertIsNotNone(self._find(
            self.root + '/SIP/CredentialLists/{}/Credentials/{}.json'.format(
                SIDS['cred_list'], SIDS['credential']), 'POST'))
        self.assertIsNotNone(self._find(
            self.root + '/SIP/CredentialLists/{}/Credentials/{}.json'.format(
                SIDS['cred_list'], SIDS['credential']), 'DELETE'))

    def test_outgoing_callerid_raw_client(self):
        self.assertIsNotNone(self._find(self.root + '/OutgoingCallerIds.json', 'GET'))
        create_req = self._find(self.root + '/OutgoingCallerIds.json', 'POST')
        self.assertIsNotNone(create_req)
        body = _parse_form(create_req['body'])
        for key in ('PhoneNumber', 'FriendlyName'):
            self.assertIn(key, body)
        self.assertIsNotNone(self._find(
            self.root + '/OutgoingCallerIds/{}.json'.format(SIDS['ocid']), 'POST'))
        self.assertIsNotNone(self._find(
            self.root + '/OutgoingCallerIds/{}.json'.format(SIDS['ocid']), 'DELETE'))


if __name__ == '__main__':
    unittest.main()
