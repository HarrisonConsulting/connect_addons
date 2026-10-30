{
    'name': 'Connect Textable',
    'version': '19.0.1.0.1',
    'category': 'Phone',
    'summary': 'Send and receive SMS through an ElectSys Textable tenant',
    'description': """
SMS carrier for Connect through ElectSys's white-label Textable tenant.

* Sends with POST /api/send using the token of the Textable user that holds
  the sender number, behind a local gate (assigned sender, approved 10DLC
  campaign, NANP destination, phone blacklist, per-number and per-recipient
  caps) and a send intent committed before the request.
* Records what is actually known: accepted is "sent, delivery unconfirmed",
  and an ambiguous outcome is never sent again.
* Receives inbound messages and outbound echoes on a per-user relay webhook
  guarded by a path token per Textable user, kept only as a hash.
""",
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': ['connect'],
    'data': [
        'security/ir.model.access.csv',
        'security/record_rules.xml',
        'views/settings_views.xml',
        'views/number_views.xml',
        'views/send_views.xml',
        'views/message_views.xml',
        'wizard/test_message_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
