-- Blank VoiceTel credentials and stop remote activity on a neutralized
-- database. Core's neutralize.sql also blanks every connect.voicetel_*
-- parameter; with no API key the webhook signature check fails closed.
UPDATE ir_config_parameter
   SET value = ''
 WHERE key IN (
    'connect.voicetel_api_key',
    'connect.display_voicetel_api_key'
 );

UPDATE ir_config_parameter
   SET value = 'False'
 WHERE key IN (
    'connect.voicetel_auto_sync',
    'connect.voicetel_fetch_call_prices'
 );

-- Neutralization runs before the module update on a copied database, so
-- the columns may not exist yet (an older connect_voicetel, or the stub
-- that kept its settings in ir_config_parameter): touch only those present.
DO $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT c.table_name, c.column_name, v.value
          FROM (VALUES
                ('connect_settings', 'voicetel_api_key', 'NULL'),
                ('connect_settings', 'display_voicetel_api_key', 'NULL'),
                ('connect_settings', 'voicetel_auto_sync', 'FALSE'),
                ('connect_settings', 'voicetel_fetch_call_prices', 'FALSE'),
                ('connect_user', 'voicetel_password', 'NULL')
               ) AS v (table_name, column_name, value)
          JOIN information_schema.columns c
            ON c.table_schema = current_schema()
           AND c.table_name = v.table_name
           AND c.column_name = v.column_name
    LOOP
        EXECUTE format('UPDATE %I SET %I = %s',
                       target.table_name, target.column_name, target.value);
    END LOOP;
END
$$;

UPDATE ir_cron
   SET active = FALSE
 WHERE id IN (
    SELECT res_id FROM ir_model_data
     WHERE module = 'connect_voicetel'
       AND name = 'voicetel_fetch_call_prices'
       AND model = 'ir.cron'
 );

-- Tables the 19.0.4.0.0 migration renamed out of the way keep the old
-- module's rows: blank anything that looks like a secret in them.
DO $$
DECLARE
    col record;
BEGIN
    FOR col IN
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name LIKE 'connect\_voicetel\_legacy\_%'
           AND (column_name LIKE '%password%' OR column_name LIKE '%secret%'
                OR column_name LIKE '%api\_key%' OR column_name LIKE '%token%')
           AND data_type IN ('character varying', 'text')
    LOOP
        EXECUTE format('UPDATE %I SET %I = NULL', col.table_name, col.column_name);
    END LOOP;
END
$$;
