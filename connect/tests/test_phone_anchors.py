# -*- coding: utf-8 -*-
"""Other modules extend connect.phone by these XPath expressions, and an inherited
template that loses its anchor breaks the whole asset bundle. They are copied from
those modules, so a change to phone.xml has to pass here first.
"""

from lxml import etree

from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_open

ANCHORS = [
    # connect_enqueue
    "//div[@t-ref='phone-header']",
    "//div[@t-ref='collapsed-icon']",
    "//div[@t-ref='collapsed-icon']/i",
    "//button[@aria-label='Audio Settings']",
    "//button[@aria-label='Audio Settings']/ancestor::div[1]",
    # connect_callout
    "//button[@title='History']",
    "//Calls/..",
    # connect_caller_intel
    "//div[contains(@t-attf-class, 'o_phone_call_details')]/t[@t-else]",
    "//span[hasclass('call-waiting-caller')]",
]


def _hasclass(context, *classes):
    node_classes = set((context.context_node.get('class') or '').split())
    return node_classes.issuperset(classes)


@tagged('post_install', '-at_install')
class TestPhoneAnchors(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with file_open('connect/static/src/components/phone/phone/phone.xml', 'rb') as template:
            templates = etree.fromstring(template.read())
        cls.phone = templates.xpath("t[@t-name='connect.phone']")[0]

    def _resolve(self, anchor):
        evaluate = etree.XPathEvaluator(
            self.phone, extensions={(None, 'hasclass'): _hasclass})
        return evaluate(anchor)

    def test_every_anchor_resolves(self):
        for anchor in ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertEqual(len(self._resolve(anchor)), 1)

    def test_the_tabs_anchor_is_the_tabs(self):
        tabs = self._resolve("//button[@aria-label='Audio Settings']/ancestor::div[1]")[0]
        self.assertIn('o_phone_tabs', tabs.get('t-attf-class'))

    def test_the_row_follows_the_tabs_and_only_with_two_lines(self):
        """The Work body lands between the tabs and the row, so the row hides there."""
        tabs = self._resolve("//button[@aria-label='Audio Settings']/ancestor::div[1]")[0]
        row = tabs.xpath("following-sibling::*[not(self::comment())][1]")[0]
        self.assertEqual(row.tag, 'LineSelector')
        condition = row.get('t-if')
        for part in ('isMultiLine', "state.activeTab !== 'work'", '!state.isAudioSettings'):
            self.assertIn(part, condition)
