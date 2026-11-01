import logging

logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Clear the way for the NULL-safe unique indexes on connect.audio.utterance.

    Utterances are a regenerable cache, so the newest row per cache key wins
    and the rest go, attachment and all. Runs before the schema pass: a
    database that accumulated duplicates while voice_id was NULL (PostgreSQL
    lets NULLs collide freely in a plain UNIQUE) would otherwise refuse the
    new indexes.

    Delete once every database that ran connect below 1.29.3 has upgraded.
    """
    cr.execute("SELECT to_regclass('connect_audio_utterance')")
    if not cr.fetchone()[0]:
        return
    cr.execute("""
        SELECT id
          FROM (SELECT id,
                       row_number() OVER (
                           PARTITION BY audio_id, voice_id, text_hash,
                                        params_hash
                           ORDER BY generated_on DESC NULLS LAST, id DESC
                       ) AS rn
                  FROM connect_audio_utterance) ranked
         WHERE rn > 1
    """)
    stale_ids = tuple(row[0] for row in cr.fetchall())
    if not stale_ids:
        return
    cr.execute("""
        DELETE FROM ir_attachment
         WHERE res_model = 'connect.audio.utterance'
           AND res_field = 'file'
           AND res_id IN %s
    """, (stale_ids,))
    cr.execute("DELETE FROM connect_audio_utterance WHERE id IN %s",
               (stale_ids,))
    logger.info('Dropped %d duplicate connect.audio.utterance cache rows',
                len(stale_ids))
