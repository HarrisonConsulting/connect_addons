{
    'name': 'Connect Caller Intel Ascertain',
    'version': '19.0.1.0.0',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Complaint-board reports on callers, researched as cited ascertain inquiries',
    'description': """
Adds the web_reports source to Connect Caller Intel. Each refresh of a number runs
one ascertain inquiry: a web search, a captured page per complaint board, and a cited
finding per report. Every mark links back to the inquiry and the captured page.
    """,
    'depends': ['connect_caller_intel', 'modelnexus_ascertain'],
    'data': [
        'data/method_data.xml',
        'data/source_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
