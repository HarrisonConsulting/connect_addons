-- connect_textable neutralization, run by Odoo on every neutralized copy.
--
-- A copy must not be able to send through Textable or accept relay posts:
-- blank every number's token halves (sends then stop at "credentials not
-- set" before any request) and every Textable user's relay secret hash
-- (relay posts then fail closed with a 404). Columns are checked first,
-- because neutralization can run before the module's tables are up to date.
DO $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT c.table_name, c.column_name
          FROM (VALUES
                ('connect_textable_number', 'account_uid'),
                ('connect_textable_number', 'api_key'),
                ('connect_textable_number', 'display_account_uid'),
                ('connect_textable_number', 'display_api_key'),
                ('connect_textable_relay', 'token_hash')
               ) AS v (table_name, column_name)
          JOIN information_schema.columns c
            ON c.table_schema = current_schema()
           AND c.table_name = v.table_name
           AND c.column_name = v.column_name
    LOOP
        EXECUTE format('UPDATE %I SET %I = NULL', target.table_name, target.column_name);
    END LOOP;
END
$$;

