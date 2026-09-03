# -*- encoding: utf-8 -*-

{
    'name': 'Connect HC Core',
    'version': '19.0.1.0.0',
    'author': 'Harrison Consulting, LLC',
    'website': 'https://www.harrison.consulting',
    'license': 'OPL-1',
    'category': 'Phone',
    'summary': 'Audio library, voicemail, parking and scheduling layer for Connect',
    'description': """
The Harrison Consulting layer of the Connect telephony suite: the audio
library and its TTS abstraction, voices and cached utterances, voicemail
boxes and stages, call parking, message conversations, business-hours
schedule rules, and the in-app documentation browser.

The provider module underneath supplies routing — numbers, callflows,
extensions, TwiML. This module owns everything a call SAYS and everything
it leaves behind, and attaches those to the provider's routing records
through inherited extensions rather than by editing them.
    """,
    'depends': ['connect'],
    'data': [
        # Security
        'security/admin.xml',
        'security/user.xml',
        'security/webhook.xml',
        'security/user_record_rules.xml',
        'security/admin_record_rules.xml',
        # Data (post-security)
        'data/schedule_data.xml',
        'data/audio.xml',
        'data/voicemail_stage_data.xml',
        'data/ir_cron.xml',
        # Views
        'views/audio.xml',
        'views/voicemail_box.xml',
        'views/voicemail.xml',
        'views/park_slot.xml',
        'views/schedule.xml',
        'views/callflow.xml',
        'views/settings.xml',
        'views/twiml.xml',
        'views/user.xml',
        'views/documentation.xml',
        # Wizard
        'wizard/conversation_wizard_views.xml',
        # Conversation views (after wizard, references wizard action)
        'views/conversation.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/connect_hc_core/static/src/widgets/audio_recorder/*',
        ],
    },
    'installable': True,
    'application': False,
    # connect's routing methods read the audio fields declared here, so the
    # two are never usefully installed apart.
    'auto_install': True,
    'post_init_hook': 'post_init_hook',
}
