{
    'name': 'Connect Caller Intel Monitor',
    'version': '19.0.1.0.0',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Monitor signal and alert when an external report lands on one of our numbers',
    'description': """
Adds a durable ai.monitor.signal and a notification alert to the caller-intel
admin notice for our own numbers. One open signal per number collects its
reports; clearing the number closes the signal.
    """,
    'depends': ['connect_caller_intel', 'modelnexus_ai_monitor'],
    'data': [
        'data/monitor_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
