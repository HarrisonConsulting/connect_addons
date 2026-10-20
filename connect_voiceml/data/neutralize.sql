-- Blank VoiceML credentials and stop provider sync on a neutralized database.
-- voiceml_account_sid and voiceml_verify_requests are left alone: the sid is
-- an identifier, and with no API key the webhook signature check fails closed.
UPDATE ir_config_parameter
   SET value = ''
 WHERE key IN (
    'connect.voiceml_api_key',
    'connect.display_voiceml_api_key'
 );

UPDATE ir_config_parameter
   SET value = 'False'
 WHERE key = 'connect.voiceml_auto_sync';

UPDATE connect_settings
   SET voiceml_api_key = NULL,
       display_voiceml_api_key = NULL,
       voiceml_auto_sync = FALSE;

UPDATE connect_user
   SET voiceml_password = NULL;
