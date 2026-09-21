"""Assert KEEP/MOVE/PBX row counts after the 19.0.4.4.0 cutover.

Reads the snapshot written by pre-migration. MOVE tables are compared
under their new names. PBX tables must still exist. Zero `_legacy` or
`_archive` tables. Coalesces stray callflow.language values onto the
Twilio Selection (folded here from NG 19.0.3.1.2, which targeted the
old connect_callflow name).
"""

import logging

_logger = logging.getLogger(__name__)

COUNTS_TABLE = '_connect_ng_cutover_counts'

KEEP_TABLES = (
    'connect_call',
    'connect_user',
    'connect_recording',
    'connect_channel',
    'connect_message',
    'connect_schedule',
    'connect_settings',
    'connect_favorite',
    'connect_debug',
)

MOVE_TABLES = (
    ('connect_exten', 'connect_twilio_exten'),
    ('connect_callflow', 'connect_twilio_callflow'),
    ('connect_callflow_choice', 'connect_twilio_callflow_choice'),
    ('connect_number', 'connect_twilio_number'),
    ('connect_outgoing_callerid', 'connect_twilio_outgoing_callerid'),
    ('connect_user_callflow', 'connect_twilio_user_callflow'),
    ('connect_user_callflow_call', 'connect_twilio_user_callflow_call'),
    ('connect_message_configuration', 'connect_twilio_message_configuration'),
    ('connect_domain', 'connect_twilio_domain'),
    ('connect_twiml', 'connect_twilio_twiml'),
)

PBX_TABLES = (
    'connect_audio',
    'connect_schedule_line',
    'connect_schedule_holiday',
    'connect_documentation',
    'connect_scheduled_call',
    'connect_voicemail_box',
    'connect_park_slot',
    'connect_conversation',
)

COUNT_KPI = (
    'connect_call',
    'connect_user',
    'connect_number',
    'connect_recording',
)

KPI_NEW_NAME = {
    'connect_number': 'connect_twilio_number',
}

NG_LEGACY_TABLES = (
    '_connect_exten_legacy',
    '_connect_callflow_legacy',
    '_connect_callflow_choice_legacy',
    '_connect_callflow_connect_user_rel_legacy',
    '_connect_number_legacy',
    '_connect_endpoint_legacy',
    '_connect_outgoing_callerid_legacy',
    '_connect_user_callflow_legacy',
    '_connect_user_callflow_call_legacy',
    '_connect_message_configuration_legacy',
)

NG_ARCHIVE_TABLES = (
    '_connect_pbx_group_archive',
    '_connect_scheduled_call_archive',
    '_connect_transcription_rule_archive',
    '_connect_documentation_archive',
    '_connect_pbx_group_connect_user_rel_archive',
    '_connect_user_connect_pbx_group_rel_archive',
    '_connect_pbx_group_res_users_rel_archive',
)

LANGUAGE_ALLOWED = {
    'ca-ES', 'cs-CZ', 'da-DK', 'de-DE', 'en-GB', 'en-US', 'es-ES',
    'es-MX', 'fi-FI', 'fr-FR', 'hu-HU', 'is-IS', 'it-IT', 'nl-BE',
    'nl-NL', 'pl-PL', 'pt-BR', 'pt-PT', 'ro-RO', 'ru-RU', 'sk-SK',
    'sv-SE', 'tr-TR', 'uk-UA', 'vi-VN', 'zh-CN',
}


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return bool(cr.fetchone())


def _count(cr, table):
    if not _table_exists(cr, table):
        return None
    cr.execute('SELECT COUNT(*) FROM "%s"' % table)
    return cr.fetchone()[0]


def _assert_count(cr, table, expected):
    n = _count(cr, table)
    if expected is None and n is None:
        return
    if n != expected:
        raise AssertionError('%s: %s != %s' % (table, n, expected))


def _snapshot(cr):
    if not _table_exists(cr, COUNTS_TABLE):
        raise AssertionError(
            'cutover snapshot table %s missing; pre-migration did not run'
            % COUNTS_TABLE
        )
    cr.execute('SELECT table_name, row_count FROM %s' % COUNTS_TABLE)
    return {row[0]: row[1] for row in cr.fetchall()}


def _coalesce_callflow_language(cr):
    table = 'connect_twilio_callflow'
    if not _table_exists(cr, table):
        return
    cr.execute('SELECT DISTINCT language FROM "%s" WHERE language IS NOT NULL' % table)
    existing = {row[0] for row in cr.fetchall()}
    stale = existing - LANGUAGE_ALLOWED
    if not stale:
        return
    _logger.warning(
        'coalescing %s out-of-selection callflow language value(s) to en-US: %s',
        len(stale), sorted(stale),
    )
    cr.execute(
        """
        UPDATE connect_twilio_callflow
           SET language = 'en-US'
         WHERE language = ANY(%s)
        """,
        (list(stale),),
    )


def _assert_pbx_table_counts(cr, snapshot, tables=PBX_TABLES):
    for table in tables:
        _assert_count(cr, table, snapshot.get(table))


def migrate(cr, version):
    if not version:
        return

    snap = _snapshot(cr)

    for table in KEEP_TABLES:
        _assert_count(cr, table, snap.get(table))

    for src, dst in MOVE_TABLES:
        expected = snap.get(src)
        if expected is None and snap.get(dst) is not None:
            expected = snap.get(dst)
        _assert_count(cr, dst, expected)

    _assert_pbx_table_counts(cr, snap)

    for old in COUNT_KPI:
        new = KPI_NEW_NAME.get(old, old)
        _assert_count(cr, new, snap.get(old))

    forbidden = [t for t in NG_LEGACY_TABLES + NG_ARCHIVE_TABLES if _table_exists(cr, t)]
    if forbidden:
        raise AssertionError(
            'archive/legacy tables present after cutover: %s' % (forbidden,)
        )

    _coalesce_callflow_language(cr)
    _logger.info('connect 19.0.4.4.0 post-migration complete')
