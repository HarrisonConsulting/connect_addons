-- Blank Twilio credentials and stop provider sync on a neutralized database.
UPDATE ir_config_parameter
   SET value = ''
 WHERE key IN (
    'connect_twilio.account_sid',
    'connect_twilio.auth_token',
    'connect_twilio.api_key',
    'connect_twilio.api_secret'
 );

UPDATE ir_config_parameter
   SET value = 'False'
 WHERE key = 'connect_twilio.auto_sync';

UPDATE connect_settings
   SET account_sid = NULL,
       auth_token = NULL,
       display_auth_token = NULL,
       twilio_api_key = NULL,
       twilio_api_secret = NULL,
       display_twilio_api_secret = NULL,
       region_auth_token = NULL,
       display_region_auth_token = NULL,
       twilio_auto_sync = FALSE;

-- Twilio SIP credential passwords on users.
UPDATE connect_user
   SET password = NULL;
