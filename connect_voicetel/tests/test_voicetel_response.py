# -*- coding: utf-8 -*-

from odoo.tests import TransactionCase, tagged

from ..models.voicetel_response import (
    Client,
    Connect,
    Dial,
    Gather,
    Sip,
    VoiceResponse,
    apply_say_voice,
    pretty_xml,
)

DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'


@tagged('post_install', '-at_install')
class TestVoicetelResponse(TransactionCase):
    """Exact-shape assertions for the VoiceTel call-control XML builder.

    No `twilio` import at module scope: the package is only ever used
    inside test_matches_twilio_reference_output, as an oracle, and that
    import is guarded so the rest of this suite runs without it installed.
    """

    def test_empty_response(self):
        self.assertEqual(str(VoiceResponse()), DECLARATION + '<Response />')

    def test_say_renders_attrs_sorted_and_escapes_text(self):
        response = VoiceResponse()
        response.say('Hello & <World> "quoted"', voice='Woman', language='en-US')
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Say language="en-US" voice="Woman">'
            'Hello &amp; &lt;World&gt; "quoted"</Say></Response>',
        )

    def test_attribute_values_escape_quotes(self):
        response = VoiceResponse()
        response.say('hi', voice='a"b')
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Say voice="a&quot;b">hi</Say></Response>',
        )

    def test_pause_hangup_record_reject_redirect_play(self):
        response = VoiceResponse()
        response.pause(length=1)
        response.record(maxLength=120, finishOnKey='#', playBeep=True,
                         recordingStatusCallback='/rsc')
        response.play('http://example.com/a.mp3')
        response.redirect('/next?x=1&y=2')
        response.reject(reason='busy')
        response.hangup()
        self.assertEqual(
            str(response),
            DECLARATION + '<Response>'
            '<Pause length="1" />'
            '<Record finishOnKey="#" maxLength="120" playBeep="true" '
            'recordingStatusCallback="/rsc" />'
            '<Play>http://example.com/a.mp3</Play>'
            '<Redirect>/next?x=1&amp;y=2</Redirect>'
            '<Reject reason="busy" />'
            '<Hangup />'
            '</Response>',
        )

    def test_gather_nests_say(self):
        response = VoiceResponse()
        gather = Gather(action='/g', method='POST', timeout=5, numDigits='1',
                         input='dtmf speech', language='en-US', hints='sales, support')
        gather.say('Welcome', language='en-US', voice='Woman')
        response.append(gather)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Gather action="/g" hints="sales, support" '
            'input="dtmf speech" language="en-US" method="POST" numDigits="1" '
            'timeout="5"><Say language="en-US" voice="Woman">Welcome</Say>'
            '</Gather></Response>',
        )

    def test_dial_sip_convenience_method(self):
        response = VoiceResponse()
        dial = Dial(callerId='+15551234567', action='/action')
        dial.sip('sip:user@example.com', statusCallbackEvent='answered completed',
                  statusCallback='/status')
        response.append(dial)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Dial action="/action" '
            'callerId="+15551234567"><Sip statusCallback="/status" '
            'statusCallbackEvent="answered completed">sip:user@example.com</Sip>'
            '</Dial></Response>',
        )

    def test_dial_number_convenience_method(self):
        response = VoiceResponse()
        dial = Dial(timeout=60, callerId='+15551234567', timeLimit=3600)
        dial.number('+15559876543', statusCallbackEvent='answered completed',
                    statusCallback='/status')
        response.append(dial)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Dial callerId="+15551234567" '
            'timeLimit="3600" timeout="60"><Number statusCallback="/status" '
            'statusCallbackEvent="answered completed">+15559876543</Number>'
            '</Dial></Response>',
        )

    def test_dial_bare_body_is_a_plain_number(self):
        response = VoiceResponse()
        response.append(Dial('+15551234567'))
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Dial>+15551234567</Dial></Response>',
        )

    def test_dial_conference(self):
        response = VoiceResponse()
        dial = Dial()
        dial.conference('user-1-2')
        response.append(dial)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Dial><Conference>user-1-2</Conference>'
            '</Dial></Response>',
        )

    def test_dial_appends_standalone_sip(self):
        response = VoiceResponse()
        dial = Dial()
        dial.append(Sip('sip:user@devmax17.sip.voicetel.com'))
        response.append(dial)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Dial><Sip>sip:user@devmax17.sip.'
            'voicetel.com</Sip></Dial></Response>',
        )

    def test_dial_client_with_identity_and_parameter(self):
        response = VoiceResponse()
        dial = Dial(callerId='+15551234567', action='http://example.com/action',
                    record='record-from-answer-dual',
                    recordingStatusCallback='http://example.com/rsc')
        client = Client(statusCallbackEvent='answered completed',
                         statusCallback='http://example.com/status')
        client.identity('user-1')
        client.parameter(name='CallerName', value='Alice')
        dial.append(client)
        response.append(dial)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Dial action="http://example.com/action" '
            'callerId="+15551234567" record="record-from-answer-dual" '
            'recordingStatusCallback="http://example.com/rsc">'
            '<Client statusCallback="http://example.com/status" '
            'statusCallbackEvent="answered completed">'
            '<Identity>user-1</Identity>'
            '<Parameter name="CallerName" value="Alice" />'
            '</Client></Dial></Response>',
        )

    def test_enqueue_is_the_hold_music_primitive(self):
        response = VoiceResponse()
        response.enqueue('support', waitUrl='/wait.xml')
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Enqueue waitUrl="/wait.xml">support'
            '</Enqueue></Response>',
        )

    def test_pay(self):
        response = VoiceResponse()
        response.pay(chargeAmount='10.00')
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Pay chargeAmount="10.00" /></Response>',
        )

    def test_connect_supports_generic_append(self):
        response = VoiceResponse()
        connect = Connect()
        connect.append(Gather(action='/g'))
        response.append(connect)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Connect><Gather action="/g" /></Connect>'
            '</Response>',
        )

    def test_none_valued_attrs_are_omitted(self):
        response = VoiceResponse()
        response.record(maxLength=120, finishOnKey=None, playBeep=True)
        self.assertEqual(
            str(response),
            DECLARATION + '<Response><Record maxLength="120" '
            'playBeep="true" /></Response>',
        )

    def test_apply_say_voice_backfills_missing_voice_only(self):
        response = VoiceResponse()
        response.say('has one', voice='Man')
        response.say('needs one')
        rendered = apply_say_voice(str(response), 'Woman')
        self.assertIn('<Say voice="Man">has one</Say>', rendered)
        self.assertIn('<Say voice="Woman">needs one</Say>', rendered)

    def test_apply_say_voice_noop_without_say(self):
        response = VoiceResponse()
        response.hangup()
        original = str(response)
        self.assertEqual(apply_say_voice(original, 'Woman'), original)

    def test_apply_say_voice_passthrough_on_bad_input(self):
        self.assertIsNone(apply_say_voice(None, 'Woman'))
        self.assertEqual(apply_say_voice('not xml', 'Woman'), 'not xml')

    def test_pretty_xml_indents_without_blank_lines(self):
        response = VoiceResponse()
        response.say('hi')
        pretty = pretty_xml(str(response))
        self.assertIn('<Say>hi</Say>', pretty)
        self.assertNotIn('\n\n', pretty)

    def test_matches_twilio_reference_output(self):
        """Byte-for-byte oracle: the same calls against the real twilio
        package must produce the same XML this builder produces, for
        every shape our callflows actually render (measured against
        connect_twilio/connect_pbx `origin/19.0`: VoiceResponse.say/pause/
        record/play/redirect/reject/hangup, Gather>Say, Dial>Sip,
        Dial>Number, Dial>Conference, Dial>Client>Identity+Parameter, and a
        bare-string Dial)."""
        try:
            from twilio.twiml.voice_response import (
                Client as TwClient,
                Dial as TwDial,
                Gather as TwGather,
                VoiceResponse as TwVoiceResponse,
            )
        except ImportError:
            self.skipTest(
                'twilio package not importable in this test environment; '
                'every other assertion in this suite still ran')

        def build(voiceresponse_cls, dial_cls, gather_cls, client_cls):
            response = voiceresponse_cls()
            response.say('Welcome to our company!', voice='Woman', language='en-US')
            response.pause(length=1)

            gather = gather_cls(action='/gather', method='POST', timeout=5,
                                 numDigits='1', input='dtmf speech',
                                 language='en-US', hints='sales, support')
            gather.say('Please choose an option', language='en-US', voice='Woman')
            response.append(gather)

            dial_sip = dial_cls(callerId='+15551234567', action='/action',
                                 record='record-from-answer-dual',
                                 recordingStatusCallback='/rsc')
            dial_sip.sip('sip:user@example.com',
                         statusCallbackEvent='answered completed',
                         statusCallback='/status')
            response.append(dial_sip)

            dial_number = dial_cls(timeout=60, callerId='+15551234567',
                                    timeLimit=3600)
            dial_number.number('+15559876543',
                                statusCallbackEvent='answered completed',
                                statusCallback='/status')
            response.append(dial_number)

            dial_conf = dial_cls()
            dial_conf.conference('user-1-2')
            response.append(dial_conf)

            dial_client = dial_cls(callerId='+15551234567', action='/action2')
            client = client_cls(statusCallbackEvent='answered completed',
                                 statusCallback='/status2')
            client.identity('user-1')
            client.parameter(name='CallerName', value='Alice')
            dial_client.append(client)
            response.append(dial_client)

            response.append(dial_cls('+15550000000'))

            response.record(maxLength=120, finishOnKey='#', playBeep=True,
                             recordingStatusCallback='/rsc2')
            response.play('http://example.com/a.mp3')
            response.redirect('/next?x=1&y=2')
            response.reject(reason='busy')
            response.hangup()
            return str(response)

        ours = build(VoiceResponse, Dial, Gather, Client)
        theirs = build(TwVoiceResponse, TwDial, TwGather, TwClient)
        self.assertEqual(ours, theirs)
