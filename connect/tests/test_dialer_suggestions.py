"""Dialer lookup keeps contact access and the destination's country/extension."""

from uuid import uuid4

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDialerSuggestions(TransactionCase):

    def setUp(self):
        super().setUp()
        self.person = new_test_user(
            self.env, login='dialer-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        self.person.partner_id.country_id = self.env.ref('base.us')
        self.dialer = self.env['connect.user'].with_user(self.person)
        self.marker = 'Dialer-%s' % uuid4().hex

    def _partner(self, **values):
        return self.env['res.partner'].create({'name': self.marker, **values})

    def test_contact_country_precedes_user_and_preserves_extension(self):
        partner = self._partner(country_id=self.env.ref('base.gb').id)
        self.assertEqual(
            self.dialer.normalize_dial_number('020 7946 0018 ext. 42', partner.id),
            '+442079460018 ext. 42')

    def test_user_then_company_country(self):
        self.env.company.country_id = self.env.ref('base.gb')
        self.assertEqual(self.dialer.normalize_dial_number('(202) 555-0123'), '+12025550123')
        self.person.partner_id.country_id = False
        self.assertEqual(self.dialer.normalize_dial_number('020 7946 0018'), '+442079460018')

    def test_international_and_non_pstn_destinations(self):
        for original, expected in (
            ('+33 1 42 68 53 00', '+33142685300'),
            ('0033 1 42 68 53 00', '+33142685300'),
            ('+442079460018x123', '+442079460018x123'),
            ('1234', '1234'),
            ('sip:alice@example.test', 'sip:alice@example.test'),
        ):
            with self.subTest(number=original):
                self.assertEqual(self.dialer.normalize_dial_number(original), expected)

    def test_lookup_by_name_deduplicates_full_phone_and_mobile(self):
        partner = self._partner(phone='(202) 555-0123', mobile='+12025550123')
        self.assertEqual(self.dialer.dialer_suggestions(self.marker), [{
            'key': '%s:+12025550123' % partner.id,
            'name': partner.display_name,
            'number': '+12025550123',
            'partner_id': partner.id,
        }])

    def test_digits_find_formatted_phone_numbers(self):
        partner = self._partner(phone='(202) 555-0197')
        matches = self.dialer.dialer_suggestions('2025550197')
        self.assertIn(partner.id, [item['partner_id'] for item in matches])

    def test_contact_rules_apply_to_lookup_and_explicit_normalization(self):
        visible = self._partner(phone='+12025550123')
        hidden = self._partner(phone='+12025550124', country_id=self.env.ref('base.gb').id)
        # A global rule deliberately excludes this one contact for ordinary
        # users; the dialer must not sudo past it to obtain its country/phone.
        self.env['ir.rule'].create({
            'name': 'Dialer hidden contact',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'domain_force': repr([('id', '!=', hidden.id)]),
        })
        matches = self.dialer.dialer_suggestions(self.marker)
        self.assertEqual([item['partner_id'] for item in matches], [visible.id])
        with self.assertRaises(AccessError):
            self.dialer.normalize_dial_number('020 7946 0018', hidden.id)

    def test_empty_query_returns_no_contacts(self):
        self._partner(phone='+12025550123')
        self.assertEqual(self.dialer.dialer_suggestions('  '), [])

    def test_numeric_lookup_returns_matching_mobile_without_unrelated_phone(self):
        partner = self._partner(phone='(202) 555-0123', mobile='(202) 555-0124')
        matches = self.dialer.dialer_suggestions('2025550124')
        self.assertEqual([item['number'] for item in matches if item['partner_id'] == partner.id],
                         ['+12025550124'])
        names = self.dialer.dialer_suggestions(self.marker)
        self.assertEqual([item['number'] for item in names], ['+12025550123', '+12025550124'])

    def test_outbound_extension_refusal_preserves_normalization_and_internal_extensions(self):
        for number in ('+12025550123x123', '(202) 555-0123 ext. 123', '+12025550123#123'):
            with self.subTest(number=number), self.assertRaisesRegex(UserError, 'in-call keypad'):
                self.dialer.validate_dial_number(number)
        self.assertEqual(self.dialer.normalize_dial_number('+12025550123x123'), '+12025550123x123')
        self.assertEqual(self.dialer.validate_dial_number('1234'), '1234')
        self.assertEqual(self.dialer.validate_dial_number('sip:alice@example.test'), 'sip:alice@example.test')

    def test_national_gb_mobile_lookup_preserves_contact_country_for_us_user(self):
        partner = self._partner(phone='020 7946 0017', mobile='020 7946 0018',
                                country_id=self.env.ref('base.gb').id)
        self.assertEqual(self.person.partner_id.country_id, self.env.ref('base.us'))
        matches = self.dialer.dialer_suggestions('02079460018')
        self.assertEqual([item for item in matches if item['partner_id'] == partner.id], [{
            'key': '%s:+442079460018' % partner.id,
            'name': partner.display_name,
            'number': '+442079460018',
            'partner_id': partner.id,
        }])
