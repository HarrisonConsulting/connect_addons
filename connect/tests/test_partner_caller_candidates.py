# -*- coding: utf-8 -*-
"""Shared numbers collapse to a handful of distinct callers."""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPartnerCallerCandidates(TransactionCase):

    def _partners(self, number, specs):
        return self.env['res.partner'].create([
            dict(spec, phone=number) for spec in specs
        ])

    def test_same_name_under_one_company_is_one_person(self):
        """A repeated name and its company collapse to the lower-id person."""
        number = '+19995550101'
        company = self.env['res.partner'].create({
            'name': 'North Yard', 'is_company': True, 'phone': number,
        })
        people = self._partners(number, [
            {'name': 'Ada North', 'parent_id': company.id},
            {'name': 'ada  north', 'parent_id': company.id},
        ])
        shown, total = self.env['res.partner'].caller_candidates(number)
        self.assertEqual(shown, people[0])
        self.assertEqual(total, 1)
        self.assertEqual(self.env['res.partner'].get_partner_by_number(number), people[0])

    def test_six_names_show_five_and_link_nobody(self):
        """More than five distinct people stay unlinked, in id order, capped at five."""
        number = '+19995550102'
        created = self._partners(number, [
            {'name': 'Caller %s' % index} for index in range(6)
        ])
        shown, total = self.env['res.partner'].caller_candidates(number)
        self.assertEqual(list(shown.ids), list(created[:5].ids))
        self.assertEqual(total, 6)
        self.assertFalse(self.env['res.partner'].get_partner_by_number(number))

    def test_company_with_two_children_names_the_children(self):
        """A company drops when its people are in the set, and two people stay unlinked."""
        number = '+19995550103'
        company = self.env['res.partner'].create({
            'name': 'Two Kids Co', 'is_company': True, 'phone': number,
        })
        children = self._partners(number, [
            {'name': 'Ada Two', 'parent_id': company.id},
            {'name': 'Bea Two', 'parent_id': company.id},
        ])
        shown, total = self.env['res.partner'].caller_candidates(number)
        self.assertEqual(set(shown.ids), set(children.ids))
        self.assertEqual(total, 2)
        self.assertFalse(self.env['res.partner'].get_partner_by_number(number))

    def test_unknown_number_matches_nobody(self):
        """A number on no contact returns an empty handful."""
        shown, total = self.env['res.partner'].caller_candidates('+19995550104')
        self.assertFalse(shown)
        self.assertEqual(total, 0)
        self.assertFalse(self.env['res.partner'].get_partner_by_number('+19995550104'))

    def test_blank_number_matches_nobody(self):
        """A blank number does not search contacts that have no phone."""
        self.env['res.partner'].create({'name': 'No Phone'})
        shown, total = self.env['res.partner'].caller_candidates(False)
        self.assertFalse(shown)
        self.assertEqual(total, 0)
        self.assertFalse(self.env['res.partner'].get_partner_by_number(''))

    def test_blank_names_stay_distinct(self):
        """Two contacts with no name do not collapse into one person."""
        number = '+19995550198'
        created = self._partners(number, [{'name': ' '}, {'name': ''}])
        shown, total = self.env['res.partner'].caller_candidates(number)
        self.assertEqual(set(shown.ids), set(created.ids))
        self.assertEqual(total, 2)

    def test_same_name_prefers_the_user(self):
        """The same normalized name keeps the contact that has a user."""
        number = '+19995550199'
        self.env['res.partner'].create({'name': 'Bea North', 'phone': number})
        linked = self.env['res.partner'].create({'name': 'Bea North', 'phone': number})
        self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Bea North',
            'login': 'bea.north.%s' % linked.id,
            'email': 'bea.north.%s@example.com' % linked.id,
            'partner_id': linked.id,
        })
        self.assertEqual(
            self.env['res.partner'].get_partner_by_number(number), linked)
