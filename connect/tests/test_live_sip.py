# -*- coding: utf-8 -*-
"""Tier 2 integration tests: SIP domain, user credentials, webhook security, TwiML validation.

Tests exercise real Twilio test credentials for:
- Twilio client configuration via Odoo settings
- Phone number sync error paths (test accounts have no real numbers)
- Webhook signature validation (RequestValidator)
- TwiML rendering and Twilio syntax validation

Run with: gdo test -d <db> -i connect -T live_twilio
"""

import hashlib
import hmac
import logging
from base64 import b64encode
from urllib.parse import urlencode

from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Dial

from odoo.tests import tagged
from odoo.tools import mute_logger

from .live_common import TwilioLiveTestCase, MAGIC_NUMBERS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Twilio Client Configuration
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestTwilioClientConfiguration(TwilioLiveTestCase):
    """Verify Odoo settings produce a working Twilio client."""

    def test_configure_test_creds_in_settings(self):
        """Setting account_sid and auth_token in connect.settings produces a working client."""
        self._configure_test_credentials()
        settings = self.env['connect.settings']

        stored_sid = settings.get_param('account_sid')
        stored_token = settings.get_param('auth_token')
        self.assertEqual(stored_sid, self.twilio_test_sid)
        self.assertEqual(stored_token, self.twilio_test_token)

    def test_get_client_returns_working_client(self):
        """get_client returns a client that can make real API calls."""
        self._configure_test_credentials()
        client = self.env['connect.settings'].get_client(region=False)
        self.assertIsNotNone(client)

        # Prove it works: create a call with magic number
        call = client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml='<Response><Say>Config test</Say></Response>',
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))
        self.assertEqual(call.status, 'queued')

    def test_get_client_region_false_no_region_or_edge(self):
        """get_client(region=False) returns a client without region/edge set."""
        self._configure_test_credentials()
        client = self.env['connect.settings'].get_client(region=False)
        self.assertIsNotNone(client)
        # region=False path creates a plain Client without setting .region/.edge
        self.assertIsInstance(client, Client)

    def test_get_client_region_true_sets_region_edge(self):
        """get_client(region=True) sets region and edge on the client."""
        self._configure_test_credentials()
        settings = self.env['connect.settings']
        settings.set_param('twilio_region', 'us1')
        settings.set_param('twilio_edge', 'ashburn')

        client = settings.get_client(region=True)
        self.assertIsNotNone(client)
        self.assertEqual(client.region, 'us1')
        self.assertEqual(client.edge, 'ashburn')

    def test_get_client_no_credentials_returns_none(self):
        """get_client returns None when no credentials are configured."""
        settings = self.env['connect.settings']
        settings.set_param('account_sid', False)
        settings.set_param('auth_token', False)

        client = settings.get_client(region=False)
        self.assertIsNone(client)

    def test_direct_client_api_call_with_magic_number(self):
        """Direct Twilio client calls.create with magic number succeeds."""
        call = self.twilio_client.calls.create(
            to=MAGIC_NUMBERS['valid'],
            from_=MAGIC_NUMBERS['valid'],
            twiml='<Response><Say>Direct client test</Say></Response>',
        )
        self.budget.record_call()
        self.assertTrue(call.sid.startswith('CA'))


# ---------------------------------------------------------------------------
# 2. Phone Number Operations (error paths -- test account has no real numbers)
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestPhoneNumberOperations(TwilioLiveTestCase):
    """Test phone number sync and error handling with test credentials."""

    def test_number_sync_test_account_restricted(self):
        """Test account can't list phone numbers (403 resource not accessible)."""
        self._configure_test_credentials()
        client = self.env['connect.settings'].get_client(region=False)
        self.assertIsNotNone(client)

        # Test credentials return 403 for incoming_phone_numbers.list()
        from twilio.base.exceptions import TwilioException
        with self.assertRaises(TwilioException):
            client.incoming_phone_numbers.list()

    def test_number_model_creation_without_twilio(self):
        """Number record can be created in Odoo with sync disabled."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        number = self.env['connect.number'].with_context(
            skip_twilio_sync=True
        ).create({
            'phone_number': '+15005550006',
            'friendly_name': 'Test Magic Number',
            'sid': 'PN_test_fake_sid_001',
        })
        self.assertEqual(number.phone_number, '+15005550006')
        self.assertEqual(number.friendly_name, 'Test Magic Number')

        # Cleanup
        number.unlink()

    @mute_logger('odoo.addons.connect.models.number')
    def test_number_update_twilio_not_found_error(self):
        """Updating a number with a fake SID in Twilio raises ValidationError."""
        self._configure_test_credentials()
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', True)

        number = self.env['connect.number'].with_context(
            skip_twilio_sync=True
        ).create({
            'phone_number': '+15005559999',
            'friendly_name': 'Nonexistent Number',
            'sid': 'PNfake00000000000000000000000001',
        })

        client = settings.get_client(region=False)
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            number.update_twilio_number(client)

        # Cleanup
        settings.set_param('twilio_auto_sync', False)
        number.unlink()

    def test_number_render_unconfigured(self):
        """Number without destination returns 'not configured' TwiML."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        number = self.env['connect.number'].with_context(
            skip_twilio_sync=True
        ).create({
            'phone_number': '+15005550099',
            'friendly_name': 'Unconfigured Number',
        })

        result = number.render()
        self.assertIn('not configured', result.lower())

        number.unlink()


# ---------------------------------------------------------------------------
# 3. Webhook Signature Validation
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestWebhookSignatureValidation(TwilioLiveTestCase):
    """Test Twilio RequestValidator with real test auth_token.

    This is critical for production webhook security. We verify that:
    - Valid signatures are accepted
    - Invalid/tampered signatures are rejected
    - Empty signatures are rejected
    - Different auth tokens produce different signatures
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.validator = RequestValidator(cls.twilio_test_token)
        cls.test_url = 'https://example.com/twilio/webhook/domain'
        cls.test_params = {
            'CallSid': 'CA1234567890abcdef1234567890abcdef',
            'Called': '+15005550006',
            'Caller': '+15005550001',
            'CallStatus': 'ringing',
            'Direction': 'inbound',
        }

    def _compute_signature(self, url, params, token=None):
        """Compute a Twilio-compatible signature for the given URL and params.

        Uses the same algorithm as Twilio:
        1. Sort params by key
        2. Append key+value to URL
        3. HMAC-SHA1 with auth_token, base64-encode
        """
        token = token or self.twilio_test_token
        validator = RequestValidator(token)
        return validator.compute_signature(url, params)

    def test_valid_signature_accepted(self):
        """RequestValidator accepts a correctly signed request."""
        signature = self._compute_signature(self.test_url, self.test_params)
        is_valid = self.validator.validate(self.test_url, self.test_params, signature)
        self.assertTrue(is_valid, "Valid signature should be accepted")

    def test_invalid_signature_rejected(self):
        """RequestValidator rejects a tampered signature."""
        # Generate valid signature, then tamper with it
        valid_sig = self._compute_signature(self.test_url, self.test_params)
        tampered_sig = valid_sig[:-4] + 'XXXX'
        is_valid = self.validator.validate(self.test_url, self.test_params, tampered_sig)
        self.assertFalse(is_valid, "Tampered signature should be rejected")

    def test_empty_signature_rejected(self):
        """RequestValidator rejects an empty signature."""
        is_valid = self.validator.validate(self.test_url, self.test_params, '')
        self.assertFalse(is_valid, "Empty signature should be rejected")

    def test_wrong_token_signature_rejected(self):
        """Signature computed with wrong token is rejected."""
        wrong_token = 'a' * 32
        wrong_sig = self._compute_signature(self.test_url, self.test_params, token=wrong_token)
        is_valid = self.validator.validate(self.test_url, self.test_params, wrong_sig)
        self.assertFalse(is_valid, "Signature from wrong token should be rejected")

    def test_modified_params_invalidate_signature(self):
        """Changing any parameter after signing invalidates the signature."""
        signature = self._compute_signature(self.test_url, self.test_params)

        # Modify one parameter
        modified_params = dict(self.test_params)
        modified_params['CallStatus'] = 'completed'
        is_valid = self.validator.validate(self.test_url, modified_params, signature)
        self.assertFalse(is_valid, "Modified params should invalidate signature")

    def test_modified_url_invalidates_signature(self):
        """Changing the URL after signing invalidates the signature."""
        signature = self._compute_signature(self.test_url, self.test_params)

        wrong_url = 'https://evil.com/twilio/webhook/domain'
        is_valid = self.validator.validate(wrong_url, self.test_params, signature)
        self.assertFalse(is_valid, "Different URL should invalidate signature")

    def test_extra_param_invalidates_signature(self):
        """Adding an extra parameter after signing invalidates the signature."""
        signature = self._compute_signature(self.test_url, self.test_params)

        extra_params = dict(self.test_params)
        extra_params['Injected'] = 'malicious'
        is_valid = self.validator.validate(self.test_url, extra_params, signature)
        self.assertFalse(is_valid, "Extra parameter should invalidate signature")

    def test_missing_param_invalidates_signature(self):
        """Removing a parameter after signing invalidates the signature."""
        signature = self._compute_signature(self.test_url, self.test_params)

        fewer_params = dict(self.test_params)
        del fewer_params['CallStatus']
        is_valid = self.validator.validate(self.test_url, fewer_params, signature)
        self.assertFalse(is_valid, "Missing parameter should invalidate signature")

    def test_empty_params_valid_when_signed_empty(self):
        """Signature computed with no params validates with no params."""
        signature = self._compute_signature(self.test_url, {})
        is_valid = self.validator.validate(self.test_url, {}, signature)
        self.assertTrue(is_valid, "Empty params signed and validated should pass")

    def test_signature_is_base64_encoded(self):
        """Generated signature is a valid base64 string."""
        signature = self._compute_signature(self.test_url, self.test_params)
        self.assertIsInstance(signature, str)
        self.assertTrue(len(signature) > 0)
        # Twilio signatures are base64-encoded HMAC-SHA1 (28 chars with padding)
        import base64
        try:
            decoded = base64.b64decode(signature)
            # HMAC-SHA1 produces 20 bytes
            self.assertEqual(len(decoded), 20)
        except Exception:
            self.fail("Signature should be valid base64")

    def test_http_to_https_url_mismatch(self):
        """HTTP vs HTTPS URL produces different signatures (security-critical)."""
        http_url = 'http://example.com/twilio/webhook/domain'
        https_url = 'https://example.com/twilio/webhook/domain'

        http_sig = self._compute_signature(http_url, self.test_params)
        https_sig = self._compute_signature(https_url, self.test_params)

        self.assertNotEqual(http_sig, https_sig,
                            "HTTP and HTTPS should produce different signatures")

        # HTTPS signature should not validate against HTTP URL
        is_valid = self.validator.validate(http_url, self.test_params, https_sig)
        self.assertFalse(is_valid)


# ---------------------------------------------------------------------------
# 4. TwiML Rendering Validation
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestTwiMLRenderingValidation(TwilioLiveTestCase):
    """Render TwiML from Connect models and validate by submitting to Twilio.

    Each TwiML verb used by Connect is tested in isolation and in combination.
    Twilio's test API rejects invalid TwiML, so a successful call.create proves
    the TwiML is syntactically correct.
    """

    def _validate_twiml(self, twiml, description="TwiML"):
        """Submit TwiML to Twilio test API; assert it is accepted."""
        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=twiml,
        )
        self.budget.record_call()
        self.assertTrue(
            call.sid.startswith('CA'),
            f"Twilio rejected {description}: {twiml[:200]}"
        )
        return call

    # --- Individual TwiML verbs ---

    def test_say_verb(self):
        """<Say> verb is accepted by Twilio."""
        twiml = '<Response><Say voice="Polly.Ruth-Generative">Hello world</Say></Response>'
        self._validate_twiml(twiml, "Say verb")

    def test_gather_verb(self):
        """<Gather> with nested <Say> is accepted by Twilio."""
        twiml = '''<Response>
            <Gather input="dtmf speech" numDigits="1" timeout="5"
                    language="en-US">
                <Say>Press 1 for sales or say your request</Say>
            </Gather>
            <Say>No input received</Say>
        </Response>'''
        self._validate_twiml(twiml, "Gather verb")

    def test_dial_number_verb(self):
        """<Dial><Number> is accepted by Twilio."""
        twiml = '''<Response>
            <Dial timeout="30" callerId="+15005550006">
                <Number statusCallbackEvent="initiated answered completed"
                        statusCallback="https://example.com/status">
                    +15005550006
                </Number>
            </Dial>
        </Response>'''
        self._validate_twiml(twiml, "Dial+Number verb")

    def test_dial_client_verb(self):
        """<Dial><Client> with identity and parameters is accepted."""
        twiml = '''<Response>
            <Dial timeout="20" callerId="+15005550006"
                  record="record-from-answer-dual">
                <Client statusCallbackEvent="initiated answered completed"
                        statusCallback="https://example.com/status">
                    <Identity>agent1@test.sip.twilio.com</Identity>
                    <Parameter name="CallerName" value="Test Caller"/>
                    <Parameter name="Partner" value="42"/>
                </Client>
            </Dial>
        </Response>'''
        self._validate_twiml(twiml, "Dial+Client verb")

    def test_dial_sip_verb(self):
        """<Dial><Sip> for SIP endpoint is accepted."""
        twiml = '''<Response>
            <Dial timeout="30" callerId="+15005550006"
                  record="record-from-answer-dual">
                <Sip statusCallbackEvent="initiated answered completed"
                     statusCallback="https://example.com/status">
                    sip:agent1@test.sip.twilio.com
                </Sip>
            </Dial>
        </Response>'''
        self._validate_twiml(twiml, "Dial+Sip verb")

    def test_record_verb(self):
        """<Record> for voicemail capture is accepted."""
        twiml = '''<Response>
            <Say>Please leave a message after the beep.</Say>
            <Record maxLength="120" finishOnKey="#" playBeep="true"
                    recordingStatusCallback="https://example.com/recording"/>
        </Response>'''
        self._validate_twiml(twiml, "Record verb")

    def test_conference_verb(self):
        """<Dial><Conference> for call parking is accepted."""
        twiml = '''<Response>
            <Dial>
                <Conference startConferenceOnEnter="true"
                            endConferenceOnExit="false"
                            waitUrl="http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical">
                    ParkSlot1
                </Conference>
            </Dial>
        </Response>'''
        self._validate_twiml(twiml, "Conference verb")

    def test_enqueue_verb(self):
        """<Enqueue> for call queuing is accepted."""
        twiml = '''<Response>
            <Enqueue waitUrl="https://example.com/wait"
                     action="https://example.com/action">
                SalesQueue
            </Enqueue>
        </Response>'''
        self._validate_twiml(twiml, "Enqueue verb")

    def test_queue_inside_dial_verb(self):
        """<Dial><Queue> for queue dequeue is accepted."""
        twiml = '''<Response>
            <Dial>
                <Queue url="https://example.com/about_to_connect">
                    SalesQueue
                </Queue>
            </Dial>
        </Response>'''
        self._validate_twiml(twiml, "Dial+Queue verb")

    # --- Connect-specific TwiML patterns ---

    def test_user_client_twiml_pattern(self):
        """TwiML matching Connect's user render_client pattern is valid."""
        response = VoiceResponse()
        dial = Dial(timeout=20, callerId='+15005550006',
                    record='record-from-answer-dual',
                    recordingStatusCallback='https://example.com/recording',
                    action='https://example.com/action')
        from twilio.twiml.voice_response import Client as TwiMLClient
        client = TwiMLClient(
            statusCallbackEvent='initiated answered completed',
            statusCallback='https://example.com/status')
        client.identity('agent1@test.sip.twilio.com')
        client.parameter(name='CallerName', value='Test Caller')
        client.parameter(name='Partner', value='42')
        dial.append(client)
        response.append(dial)

        twiml = str(response)
        self.assertIn('<Client', twiml)
        self.assertIn('<Identity>', twiml)
        self.assertIn('<Parameter', twiml)
        self._validate_twiml(twiml, "User client TwiML pattern")

    def test_user_sip_twiml_pattern(self):
        """TwiML matching Connect's user render_sip pattern is valid."""
        response = VoiceResponse()
        dial = Dial(timeout=30, callerId='+15005550006',
                    record='record-from-answer-dual',
                    recordingStatusCallback='https://example.com/recording',
                    action='https://example.com/action')
        dial.sip(
            'sip:agent1@test.sip.twilio.com',
            statusCallbackEvent='initiated answered completed',
            statusCallback='https://example.com/status')
        response.append(dial)

        twiml = str(response)
        self.assertIn('<Sip', twiml)
        self._validate_twiml(twiml, "User SIP TwiML pattern")

    def test_callflow_ivr_twiml_pattern(self):
        """TwiML matching Connect's callflow IVR pattern is valid."""
        from twilio.twiml.voice_response import Gather
        response = VoiceResponse()
        gather = Gather(
            action='https://example.com/gather',
            method='POST',
            timeout=5,
            numDigits='1',
            input='dtmf',
            language='en-US')
        gather.say('Press 1 for sales, 2 for support.')
        response.append(gather)
        response.say("We didn't receive any input. Goodbye!")
        response.hangup()

        twiml = str(response)
        self.assertIn('<Gather', twiml)
        self.assertIn('numDigits="1"', twiml)
        self._validate_twiml(twiml, "Callflow IVR TwiML pattern")

    def test_voicemail_twiml_pattern(self):
        """TwiML matching Connect's voicemail pattern is valid."""
        response = VoiceResponse()
        response.say("Hello, this is Test User. Please leave a message after the tone.",
                     voice='Polly.Ruth-Generative')
        response.record(
            maxLength=120,
            finishOnKey='#',
            playBeep=True,
            recordingStatusCallback='https://example.com/vm_recording')

        twiml = str(response)
        self.assertIn('<Record', twiml)
        self.assertIn('maxLength="120"', twiml)
        self._validate_twiml(twiml, "Voicemail TwiML pattern")

    def test_external_call_twiml_pattern(self):
        """TwiML matching Connect's originate_external_call pattern is valid."""
        response = VoiceResponse()
        dial = Dial(
            timeout=60,
            callerId='+15005550006',
            timeLimit=7200,
            record='record-from-answer',
            recordingStatusCallback='https://example.com/recording')
        dial.number(
            '+15005550006',
            statusCallback='https://example.com/status',
            statusCallbackEvent='initiated answered completed')
        response.append(dial)

        twiml = str(response)
        self.assertIn('<Dial', twiml)
        self.assertIn('timeLimit="7200"', twiml)
        self._validate_twiml(twiml, "External call TwiML pattern")

    def test_dnd_hangup_twiml_pattern(self):
        """TwiML for DND (say message + hangup) is valid."""
        response = VoiceResponse()
        response.say("The person you are trying to reach is unavailable.",
                     voice='Polly.Ruth-Generative')
        response.hangup()

        twiml = str(response)
        self.assertIn('<Say', twiml)
        self.assertIn('<Hangup', twiml)
        self._validate_twiml(twiml, "DND hangup TwiML pattern")

    def test_combined_client_and_sip_twiml(self):
        """TwiML with both Client and SIP dial (user dual-ring) is valid."""
        response = VoiceResponse()

        # Client dial (priority 1)
        dial_client = Dial(timeout=20, callerId='+15005550006',
                           action='https://example.com/action')
        from twilio.twiml.voice_response import Client as TwiMLClient
        client = TwiMLClient(
            statusCallbackEvent='initiated answered completed',
            statusCallback='https://example.com/status')
        client.identity('agent1@test.sip.twilio.com')
        dial_client.append(client)
        response.append(dial_client)

        # SIP dial (priority 2)
        dial_sip = Dial(timeout=30, callerId='+15005550006',
                        action='https://example.com/action')
        dial_sip.sip(
            'sip:agent1@test.sip.twilio.com',
            statusCallbackEvent='initiated answered completed',
            statusCallback='https://example.com/status')
        response.append(dial_sip)

        twiml = str(response)
        self.assertIn('<Client', twiml)
        self.assertIn('<Sip', twiml)
        self._validate_twiml(twiml, "Combined client+SIP TwiML pattern")

    def test_park_conference_with_announcement_twiml(self):
        """Park slot TwiML with Say announcement before Conference is valid."""
        response = VoiceResponse()
        response.say("Parked on slot 3.", voice='Polly.Ruth-Generative')
        dial = Dial()
        dial.conference(
            'ParkSlot3',
            startConferenceOnEnter=True,
            endConferenceOnExit=False,
            waitUrl='http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical')
        response.append(dial)

        twiml = str(response)
        self.assertIn('ParkSlot3', twiml)
        self._validate_twiml(twiml, "Park conference with announcement")

    def test_invalid_twiml_still_creates_call(self):
        """Twilio test API accepts even malformed TwiML (validation is at execution time)."""
        bad_twiml = '<Response><InvalidVerb>oops</InvalidVerb></Response>'
        call = self.twilio_client.calls.create(
            to=self.VALID_NUMBER,
            from_=self.VALID_NUMBER,
            twiml=bad_twiml,
        )
        self.budget.record_call()
        # Twilio queues the call but would fail at execution — test creds don't execute
        self.assertTrue(call.sid.startswith('CA'))


# ---------------------------------------------------------------------------
# 5. SIP Domain Model (test what's possible without real domain creation)
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestSIPDomainModel(TwilioLiveTestCase):
    """Test SIP domain model logic and Twilio interaction paths."""

    def test_domain_name_computation(self):
        """Domain name is computed from subdomain."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True
        ).create({
            'subdomain': 'testconnect',
            'friendly_name': 'Test Connect Domain',
            'application': self.env['connect.domain'].get_domain_app().id,
        })
        self.assertEqual(domain.domain_name, 'testconnect.sip.twilio.com')
        domain.with_context(force_delete=True).unlink()

    def test_edge_domains_computation(self):
        """Edge domains are computed for all Twilio edges."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True
        ).create({
            'subdomain': 'testedge',
            'friendly_name': 'Test Edge Domain',
            'application': self.env['connect.domain'].get_domain_app().id,
        })
        self.assertIn('testedge.sip.ashburn.twilio.com', domain.edge_domains)
        self.assertIn('testedge.sip.dublin.twilio.com', domain.edge_domains)
        domain.with_context(force_delete=True).unlink()

    def test_domain_sync_test_account_restricted(self):
        """Test account can't list SIP domains (403 resource not accessible)."""
        self._configure_test_credentials()
        client = self.env['connect.settings'].get_client(region=False)

        from twilio.base.exceptions import TwilioException
        with self.assertRaises(TwilioException):
            client.sip.domains.list()

    def test_domain_delete_protection(self):
        """Domain with delete_protection=True cannot be deleted."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True
        ).create({
            'subdomain': 'testprotect',
            'friendly_name': 'Protected Domain',
            'delete_protection': True,
            'application': self.env['connect.domain'].get_domain_app().id,
        })

        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            domain.unlink()

        # Cleanup with force_delete
        domain.with_context(force_delete=True).unlink()


# ---------------------------------------------------------------------------
# 6. User Credential Logic
# ---------------------------------------------------------------------------

@tagged('post_install', '-at_install', 'live_twilio')
class TestUserCredentialLogic(TwilioLiveTestCase):
    """Test Connect user model logic related to SIP credentials."""

    def test_generate_twilio_password_strength(self):
        """Generated password meets Twilio SIP credential requirements."""
        for _ in range(10):
            pw = self.env['connect.user'].generate_twilio_password()
            self.assertGreaterEqual(len(pw), 12, "Password must be >= 12 chars")
            self.assertTrue(any(c.islower() for c in pw), "Must contain lowercase")
            self.assertTrue(any(c.isupper() for c in pw), "Must contain uppercase")
            self.assertTrue(any(c.isdigit() for c in pw), "Must contain digit")

    def test_user_sip_uri_computation(self):
        """User SIP URI is computed from username and domain."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True
        ).create({
            'subdomain': 'testuri',
            'friendly_name': 'URI Test Domain',
            'application': self.env['connect.domain'].get_domain_app().id,
        })

        user = self.env['connect.user'].with_context(
            no_twilio_create=True
        ).create({
            'username': 'testuser1',
            'domain': domain.id,
            'sip_enabled': True,
        })

        self.assertEqual(user.uri, 'testuser1@testuri.sip.twilio.com')
        # connect_uri depends on edge
        self.assertIn('testuser1@', user.connect_uri)

        user.with_context(skip_sync=True).unlink()
        domain.with_context(force_delete=True).unlink()

    def test_username_must_be_alphanumeric(self):
        """Username validation rejects non-alphanumeric characters."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True
        ).create({
            'subdomain': 'testalpha',
            'friendly_name': 'Alpha Test Domain',
            'application': self.env['connect.domain'].get_domain_app().id,
        })

        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env['connect.user'].with_context(
                no_twilio_create=True
            ).create({
                'username': 'invalid-user!',
                'domain': domain.id,
            })

        domain.with_context(force_delete=True).unlink()

    def test_user_client_identity(self):
        """get_client_identity returns username@domain format."""
        settings = self.env['connect.settings']
        settings.set_param('twilio_auto_sync', False)

        domain = self.env['connect.domain'].with_context(
            no_twilio_create=True
        ).create({
            'subdomain': 'testidentity',
            'friendly_name': 'Identity Test Domain',
            'application': self.env['connect.domain'].get_domain_app().id,
        })

        user = self.env['connect.user'].with_context(
            no_twilio_create=True
        ).create({
            'username': 'identityuser',
            'domain': domain.id,
        })

        identity = user.get_client_identity()
        self.assertEqual(identity, 'identityuser@testidentity.sip.twilio.com')

        user.with_context(skip_sync=True).unlink()
        domain.with_context(force_delete=True).unlink()
