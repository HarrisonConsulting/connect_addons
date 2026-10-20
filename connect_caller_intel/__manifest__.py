{
    'name': 'Connect Caller Intel',
    'version': '19.0.1.0.0',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Caller reputation and spoof signals for inbound Connect calls',
    'description': """
Identifies inbound callers and raises spam and spoof signals. Every mark names
the system that reported it and how to clear it. Free sources are on by default;
paid sources stay off until an administrator accepts the cost.
    """,
    'depends': ['connect', 'connect_twilio', 'queue_job'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/source_data.xml',
        'data/queue_job_data.xml',
        'data/cron.xml',
        'views/source_views.xml',
        'views/reputation_views.xml',
        'views/call_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
