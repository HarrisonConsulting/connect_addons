# -*- coding: utf-8 -*-
"""Minimal call-control XML builder for VoiceTel.

VoiceTel's call-control XML is Twilio-compatible, but this module must not
depend on the ``twilio`` python package, so it ships its own tiny builder
covering only the verbs Connect's callflows render. The API mirrors
``twilio.twiml.voice_response`` closely enough that render code ported from
connect_twilio reads the same, and attributes are emitted in sorted order so
a rendered document is byte-identical to what the twilio package produces
for the same calls.
"""
from xml.etree import ElementTree as ET
from xml.dom.minidom import parseString


def _format_attr(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _attrs(kwargs):
    """Render kwargs as XML attribute strings, dropping unset ones and
    sorting by name so output order matches the twilio package's own
    (alphabetical) attribute serialization."""
    return {k: _format_attr(v) for k, v in sorted(kwargs.items()) if v is not None}


def apply_say_voice(content, voice):
    """Add a fallback voice to every Say that has no explicit voice."""
    if content is None or not voice:
        return content
    text = str(content)
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, TypeError, ValueError):
        return text
    changed = False
    for element in root.iter():
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 'Say' and not element.get('voice'):
            element.set('voice', voice)
            changed = True
    if not changed:
        return text
    xml = ET.tostring(root, encoding='unicode')
    if text.lstrip().startswith('<?xml'):
        return '<?xml version="1.0" encoding="UTF-8"?>' + xml
    return xml


class VoicetelXML:
    """Base call-control element. Attribute kwargs are rendered verbatim
    (camelCase names as in TwiML), sorted alphabetically."""

    tag = 'Response'

    def __init__(self, body=None, **attrs):
        self.element = ET.Element(self.tag, _attrs(attrs))
        if body is not None:
            self.element.text = str(body)

    def append(self, child):
        self.element.append(child.element)
        return child

    def _add(self, tag, body=None, **attrs):
        el = ET.SubElement(self.element, tag, _attrs(attrs))
        if body is not None:
            el.text = str(body)
        return el

    def say(self, message, **attrs):
        return self._add('Say', message, **attrs)

    def pause(self, length=1):
        return self._add('Pause', length=length)

    def hangup(self):
        return self._add('Hangup')

    def reject(self, **attrs):
        """<Reject> — no VoiceTel callflow constructs this today; kept for
        public-surface parity with twilio.twiml.voice_response.VoiceResponse
        and unverified against VoiceTel (R3 design doc §4.4)."""
        return self._add('Reject', **attrs)

    def redirect(self, url, **attrs):
        return self._add('Redirect', url, **attrs)

    def play(self, url=None, **attrs):
        return self._add('Play', url, **attrs)

    def record(self, **attrs):
        return self._add('Record', **attrs)

    def enqueue(self, name=None, **attrs):
        """<Enqueue>, the closest TwiML-compatible primitive to hold music
        (paired with a ``waitUrl`` document built from ``play``/``say``).
        No VoiceTel callflow constructs this today; support is unverified
        (R3 design doc §4.4)."""
        return self._add('Enqueue', name, **attrs)

    def pay(self, **attrs):
        """<Pay> — no VoiceTel callflow constructs this today; support is
        unverified (R3 design doc §4.4)."""
        return self._add('Pay', **attrs)

    def to_xml(self):
        return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(
            self.element, encoding='unicode')

    def __str__(self):
        return self.to_xml()


class VoiceResponse(VoicetelXML):
    tag = 'Response'


class Gather(VoicetelXML):
    tag = 'Gather'


class Dial(VoicetelXML):
    tag = 'Dial'

    def sip(self, uri, **attrs):
        return self._add('Sip', uri, **attrs)

    def number(self, number, **attrs):
        return self._add('Number', number, **attrs)

    def conference(self, name, **attrs):
        return self._add('Conference', name, **attrs)


class Sip(VoicetelXML):
    tag = 'Sip'


class Client(VoicetelXML):
    tag = 'Client'

    def identity(self, client_identity, **attrs):
        return self._add('Identity', client_identity, **attrs)

    def parameter(self, **attrs):
        return self._add('Parameter', **attrs)


class Connect(VoicetelXML):
    """<Connect> — no VoiceTel callflow constructs this today; kept for
    public-surface parity with twilio.twiml.voice_response.Connect."""
    tag = 'Connect'


def pretty_xml(content):
    try:
        dom = parseString(str(content))
        pretty_content = dom.toprettyxml()
        return "\n".join(
            [line for line in pretty_content.splitlines() if line.strip()])
    except Exception as e:
        return 'Pretty XML parse error: {}\n{}'.format(e, str(content))
