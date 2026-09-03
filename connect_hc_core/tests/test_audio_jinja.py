# -*- coding: utf-8 -*-
"""Tests for connect.audio.jinja_to_token — the migration helper that
converts legacy `{{user.name}}` Jinja templates to the new `{name}` token
form used by connect.audio.is_dynamic rendering.

Exercised by the pre-migrate that converts connect.user.voicemail_prompt
(the only Jinja-carrying text field in the migration).
"""

from odoo.tests import tagged

from odoo.addons.connect.tests.common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestJinjaToToken(ConnectTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Audio = cls.env['connect.audio']

    def test_empty_template_is_pass_through(self):
        """Empty input round-trips as 'fully convertible' so callers don't
        synthesize `[MIGRATION FAILED]` warnings for blank fields."""
        out, ok = self.Audio.jinja_to_token('', root_var='user')
        self.assertEqual(out, '')
        self.assertTrue(ok)
        out, ok = self.Audio.jinja_to_token(None, root_var='user')
        self.assertIsNone(out)
        self.assertTrue(ok)

    def test_plain_text_is_unchanged_and_convertible(self):
        """Text with no Jinja expressions returns unchanged and convertible."""
        out, ok = self.Audio.jinja_to_token(
            'Please leave a message after the tone.', root_var='user')
        self.assertEqual(out, 'Please leave a message after the tone.')
        self.assertTrue(ok)

    def test_single_token_conversion(self):
        """`{{user.name}}` becomes `{name}`."""
        out, ok = self.Audio.jinja_to_token(
            'Hello, this is {{user.name}}.', root_var='user')
        self.assertEqual(out, 'Hello, this is {name}.')
        self.assertTrue(ok)

    def test_nested_dotted_path(self):
        """Related-field paths are preserved wholesale."""
        out, ok = self.Audio.jinja_to_token(
            '{{user.partner_id.country_id.name}}', root_var='user')
        self.assertEqual(out, '{partner_id.country_id.name}')
        self.assertTrue(ok)

    def test_multiple_tokens(self):
        """All Jinja expressions convert in one pass."""
        out, ok = self.Audio.jinja_to_token(
            '{{user.name}} ({{user.username}}) is unavailable.',
            root_var='user')
        self.assertEqual(out, '{name} ({username}) is unavailable.')
        self.assertTrue(ok)

    def test_wrong_root_var_returns_unconvertible(self):
        """A Jinja var whose root doesn't match `root_var` can't be
        mechanically rewritten — flag for Jinja-fallback rendering."""
        out, ok = self.Audio.jinja_to_token(
            'Hello {{contact.name}}.', root_var='user')
        self.assertEqual(out, 'Hello {{contact.name}}.')
        self.assertFalse(ok)

    def test_jinja_control_flow_returns_unconvertible(self):
        """`{% ... %}` blocks are outside the rewrite's grammar."""
        out, ok = self.Audio.jinja_to_token(
            '{% if user.active %}Hi {{user.name}}.{% endif %}',
            root_var='user')
        self.assertFalse(ok)

    def test_filter_expression_returns_unconvertible(self):
        """Pipe filters (`|upper`) can't be represented as {token} — flag
        for pre-rendered fallback storage."""
        out, ok = self.Audio.jinja_to_token(
            'Hi {{user.name|upper}}.', root_var='user')
        self.assertFalse(ok)

    def test_identifier_with_underscores_and_digits(self):
        """Python-style identifiers (underscores, digits) in dotted paths
        stay in the token namespace."""
        out, ok = self.Audio.jinja_to_token(
            '{{user.user_id_1.display_name}}', root_var='user')
        self.assertEqual(out, '{user_id_1.display_name}')
        self.assertTrue(ok)
