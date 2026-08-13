# -*- encoding: utf-8 -*-
{
    'name': 'Connect Stripe Payments',
    'version': '19.0.2.2.3',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'PCI-compliant DTMF phone payments via Twilio <Pay> and Stripe',
    'description': """
Take card payments during live Twilio calls using DTMF masking.
Customers enter card details via keypad; agents never see or hear the digits.
Twilio tokenises the card via the Stripe Pay Connector; Odoo charges through
the standard payment.transaction pipeline (account.payment + invoice
reconciliation + refunds + token reuse all handled by Odoo's payment module).

For automatic Stripe-payout / bank-statement reconciliation and Stripe Fees
matching (requires Odoo Enterprise), install `connect_stripe_enterprise`.
    """,
    'depends': [
        'connect',
        'account_payment',
        'payment_stripe',
    ],
    'data': [
        # Security
        'security/ir.model.access.csv',
        # Views
        'views/payment_views.xml',
        'views/call_views.xml',
        'views/settings_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'connect_stripe/static/src/components/payment_dialog/*.js',
            'connect_stripe/static/src/components/payment_dialog/*.xml',
            'connect_stripe/static/src/active_calls/*.js',
            'connect_stripe/static/src/active_calls/*.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
