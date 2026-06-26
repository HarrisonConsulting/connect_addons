{
    'name': 'Connect SMS Bridge',
    'version': '19.0.1.0.7',
    'category': 'Phone',
    'summary': 'Bridge Connect Twilio telephony with Odoo SMS provider pipeline',
    'description': """
Bridges the Connect Twilio telephony module with Odoo's native sms_twilio
provider abstraction. Syncs credentials and phone numbers from Connect to
the standard SMS pipeline, enabling chatter SMS, marketing SMS, and other
Odoo-native SMS features to send through the Connect Twilio account.
""",
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'depends': ['connect', 'sms_twilio'],
    'data': [],
    'auto_install': True,
    'installable': True,
    'post_init_hook': '_post_init_hook',
}
