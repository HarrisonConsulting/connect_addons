"""Re-own the audio, voicemail, parking, conversation and schedule-rule
records to connect_hc_core before connect's own data load runs.

Moving a model between modules moves the ir.model.data rows that own its
ir.model row, every one of its ir.model.fields rows, its selection values,
its views, actions, menus and security records. Odoo deletes the rows an
updated module no longer claims — and deleting the ir.model row drops the
table with it. Re-owning them here, before the load, makes connect_hc_core
the claimant so nothing is orphaned.

Every model, field and record is named exactly. Field and selection rows are
reached by joining ir_model_fields on the model name rather than by matching
the xmlid text: in SQL LIKE, ``field_connect_audio__%`` matches
``field_connect_audio_reference__...`` too, because ``_`` is a wildcard.
"""

import logging

logger = logging.getLogger(__name__)

NEW_MODULE = 'connect_hc_core'
OLD_MODULE = 'connect'

# Models whose definition moved wholesale.
MOVED_MODELS = [
    'connect.audio',
    'connect.audio.reference',
    'connect.audio.referrer.mixin',
    'connect.audio.utterance',
    'connect.tts.mixin',
    'connect.voice',
    'connect.voicemail_box',
    'connect.voicemail_stage',
    'connect.park_slot',
    'connect.conversation',
    'connect.conversation_wizard',
    'connect.documentation',
    'connect.scheduled_call',
    'connect.schedule.line',
    'connect.schedule.holiday',
]

# Fields that stayed on a connect-owned model but are now declared by the
# audio layer. (model, field) pairs — nothing is matched by prefix.
MOVED_FIELDS = [
    ('connect.callflow', 'prompt_audio_id'),
    ('connect.callflow', 'prompt_preview'),
    ('connect.callflow', 'invalid_input_audio_id'),
    ('connect.callflow', 'invalid_input_preview'),
    ('connect.callflow', 'voicemail_audio_id'),
    ('connect.callflow', 'voicemail_preview'),
    ('connect.callflow', 'voicemail_box_id'),
    ('connect.callflow', 'after_hours_audio_id'),
    ('connect.callflow', 'after_hours_preview'),
    ('connect.call', 'voicemail_box_id'),
    ('connect.call', 'voicemail_stage_id'),
    ('connect.message', 'conversation_id'),
    ('connect.schedule', 'line_ids'),
    ('connect.schedule', 'holiday_ids'),
    ('connect.settings', 'default_twilio_voice'),
    ('connect.settings', 'park_hold_music_audio_id'),
    ('connect.settings', 'park_hold_music_audio_id_source'),
    ('connect.twiml', 'referenced_audio_ids'),
    ('connect.user', 'dnd_enabled'),
    ('connect.user', 'greeting_audio_id'),
    ('connect.user', 'greeting_preview'),
    ('connect.user', 'presence_status'),
    ('connect.user', 'presence_updated'),
    ('connect.user', 'voicemail_audio_id'),
    ('connect.user', 'voicemail_box_id'),
    ('connect.user', 'voicemail_preview'),
]

# XML records that moved, by xmlid name. Data, security, views, actions,
# menus and server actions alike — one flat allowlist, no prefix matching.
MOVED_XMLIDS = [
    # data/audio.xml
    'voice_twilio_polly_joanna',
    'voice_twilio_polly_matthew',
    'voice_twilio_polly_danielle_generative',
    'voice_twilio_polly_joanna_generative',
    'voice_twilio_polly_matthew_generative',
    'voice_twilio_polly_ruth_generative',
    'voice_twilio_polly_stephen_generative',
    'voice_twilio_alice',
    'voice_twilio_man',
    'voice_twilio_woman',
    'audio_system_transfer',
    'audio_system_connecting',
    'audio_system_dnd',
    'audio_error_no_callerid',
    'audio_error_callflow_empty',
    'audio_error_call_failed',
    'audio_error_no_extension',
    'audio_error_choice_error',
    'audio_fallback_archived',
    'audio_fallback_unresolved',
    'audio_qa_recording_notice',
    'qa_recording_notice_audio_param',
    # data/ir_cron.xml
    'ir_cron_park_timeout',
    # data/schedule_data.xml
    'schedule_line_monday',
    'schedule_line_tuesday',
    'schedule_line_wednesday',
    'schedule_line_thursday',
    'schedule_line_friday',
    'schedule_line_saturday',
    'schedule_line_sunday',
    'schedule_holiday_new_year',
    'schedule_holiday_independence_day',
    'schedule_holiday_christmas',
    'schedule_holiday_memorial_day',
    'schedule_holiday_labor_day',
    'schedule_holiday_thanksgiving',
    # data/voicemail_stage_data.xml
    'voicemail_stage_pending',
    'voicemail_stage_working',
    'voicemail_stage_review',
    'voicemail_stage_completed',
    # security/admin.xml
    'connect_conversation_wizard_admin',
    'connect_voicemail_stage_admin',
    'connect_voicemail_box_admin',
    'connect_conversation_admin',
    'connect_schedule_line_admin',
    'connect_schedule_holiday_admin',
    'connect_audio_admin',
    'connect_audio_utterance_admin',
    'connect_audio_reference_admin',
    'connect_voice_admin',
    'access_connect_documentation',
    # security/admin_record_rules.xml
    'connect_conversation_admin_rule',
    'connect_voicemail_box_admin_rule',
    # security/user.xml
    'connect_conversation_wizard_user',
    'connect_conversation_user',
    'connect_voicemail_stage_user',
    'connect_voicemail_box_user',
    'connect_schedule_line_user',
    'connect_schedule_holiday_user',
    'connect_park_slot_user',
    'connect_audio_user',
    'connect_audio_utterance_user',
    'connect_audio_reference_user',
    'connect_voice_user',
    # security/user_record_rules.xml
    'connect_call_user_rule',
    'connect_voicemail_box_user_rule',
    'connect_conversation_user_rule',
    # security/webhook.xml
    'connect_schedule_line_webhook',
    'connect_schedule_holiday_webhook',
    'connect_conversation_webhook',
    'connect_audio_webhook',
    'connect_audio_utterance_webhook',
    'connect_voice_webhook',
    # views/audio.xml
    'connect_audio_form',
    'connect_audio_list',
    'connect_audio_kanban',
    'connect_audio_search',
    'connect_audio_action',
    'connect_audio_utterance_list',
    'connect_audio_utterance_action',
    'connect_voice_list',
    'connect_voice_form',
    'connect_voice_action',
    'connect_audio_reference_list',
    'connect_audio_reference_search',
    'connect_audio_reference_action',
    'connect_audio_server_action_refresh_references',
    'connect_audio_server_action_check_reachability',
    'connect_audio_menu',
    'connect_audio_utterance_menu',
    'connect_voice_menu_item',
    # views/conversation.xml
    'action_connect_conversation',
    'connect_conversation_menu',
    'view_connect_conversation_search',
    'view_connect_conversation_kanban',
    'view_connect_conversation_list',
    'view_connect_conversation_form',
    # views/documentation.xml
    'view_connect_documentation_form',
    'action_connect_documentation',
    'connect_documentation',
    # views/park_slot.xml
    'park_slot_action',
    'park_slot_menu',
    'connect_park_slot_list',
    'connect_park_slot_form',
    'connect_park_slot_search',
    # views/voicemail.xml
    'voicemail_stage_list',
    'voicemail_stage_form',
    'voicemail_stage_action',
    'voicemail_kanban',
    'voicemail_form',
    'voicemail_list',
    'voicemail_search',
    'voicemail_action',
    'connect_voicemails_menu',
    'connect_voicemail_stages_menu',
    # views/voicemail_box.xml
    'voicemail_box_list',
    'voicemail_box_form',
    'voicemail_box_search',
    'voicemail_box_action',
    'connect_voicemail_box_menu',
    # wizard/conversation_wizard_views.xml
    'view_connect_conversation_wizard_form',
    'action_connect_conversation_wizard',
]


def migrate(cr, version):
    if not version:
        return

    # 1. ir.model rows for the moved models.
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model m
         WHERE d.model = 'ir.model'
           AND d.res_id = m.id
           AND d.module = %s
           AND m.model IN %s
        """,
        (NEW_MODULE, OLD_MODULE, tuple(MOVED_MODELS)),
    )
    logger.info('connect_hc_core: re-owned %s ir.model rows', cr.rowcount)

    # 2. ir.model.fields rows — every field of a moved model, plus the named
    #    fields that moved off a model that stayed.
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model_fields f
         WHERE d.model = 'ir.model.fields'
           AND d.res_id = f.id
           AND d.module = %s
           AND (f.model IN %s OR (f.model, f.name) IN %s)
        """,
        (NEW_MODULE, OLD_MODULE, tuple(MOVED_MODELS), tuple(MOVED_FIELDS)),
    )
    logger.info('connect_hc_core: re-owned %s ir.model.fields rows', cr.rowcount)

    # 3. ir.model.fields.selection rows hanging off those fields.
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE d.model = 'ir.model.fields.selection'
           AND d.res_id = s.id
           AND d.module = %s
           AND (f.model IN %s OR (f.model, f.name) IN %s)
        """,
        (NEW_MODULE, OLD_MODULE, tuple(MOVED_MODELS), tuple(MOVED_FIELDS)),
    )
    logger.info('connect_hc_core: re-owned %s selection rows', cr.rowcount)

    # 4. The XML records themselves.
    cr.execute(
        """
        UPDATE ir_model_data
           SET module = %s
         WHERE module = %s
           AND name IN %s
        """,
        (NEW_MODULE, OLD_MODULE, tuple(MOVED_XMLIDS)),
    )
    logger.info('connect_hc_core: re-owned %s xml records', cr.rowcount)

    # 4b. The QA-notice indirection parameter is noupdate data: an existing
    #     database keeps the value it was seeded with, which names the audio
    #     by its old xmlid.
    cr.execute(
        """
        UPDATE ir_config_parameter
           SET value = 'connect_hc_core.audio_qa_recording_notice'
         WHERE key = 'connect.qa_recording_notice_audio'
           AND value = 'connect.audio_qa_recording_notice'
        """
    )
    logger.info('connect_hc_core: repointed %s qa-notice parameters', cr.rowcount)

    # 5. Constraints and m2m relation tables follow their model, so the
    #    uninstall bookkeeping drops them with connect_hc_core rather than
    #    with connect.
    cr.execute("SELECT id FROM ir_module_module WHERE name = %s", (NEW_MODULE,))
    row = cr.fetchone()
    if row:
        module_id = row[0]
        cr.execute(
            """
            UPDATE ir_model_constraint c
               SET module = %s
              FROM ir_model m
             WHERE c.model = m.id
               AND m.model IN %s
            """,
            (module_id, tuple(MOVED_MODELS)),
        )
        logger.info('connect_hc_core: re-owned %s constraints', cr.rowcount)
        cr.execute(
            "UPDATE ir_model_relation SET module = %s WHERE name = %s",
            (module_id, 'connect_voicemail_box_member_rel'),
        )
        logger.info('connect_hc_core: re-owned %s m2m relations', cr.rowcount)
    else:
        # connect_hc_core has no ir_module_module row yet on the very first
        # upgrade that introduces it; the reflection pass on install claims
        # the constraints and relations itself.
        logger.info(
            'connect_hc_core not yet registered; constraint and relation '
            'ownership will be claimed on install.'
        )
