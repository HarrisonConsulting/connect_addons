-- Connect (Twilio) neutralization.
--
-- Run automatically by Odoo's database neutralization
-- (odoo.modules.neutralize / `odoo neutralize`) on every neutralized copy
-- — odoo.sh staging & dev builds, and any restore neutralized on purpose.
--
-- Scrubs the stored Twilio (and OpenAI) credentials so a non-production
-- copy CANNOT authenticate to the live carrier/API. connect.settings
-- get_client() returns None when auth_token is empty (it guards on it),
-- and every caller already tolerates a missing client — so no outbound
-- call, SMS, recording fetch, or AI request can leave a neutralized DB.
-- This also removes prod secrets from the copy and closes the shared-
-- account hazard where a staging test call could join a production Twilio
-- conference.
--
-- account_sid is intentionally LEFT intact: it is an identifier, not a
-- secret, and an empty auth_token alone fully disables the client — while
-- keeping it lets ops see which account the copy descends from.

UPDATE connect_settings
   SET auth_token = NULL,
       region_auth_token = NULL,
       twilio_api_key = NULL,
       twilio_api_secret = NULL,
       twilio_balance = NULL,
       openai_api_key = NULL,
       display_auth_token = NULL,
       display_region_auth_token = NULL,
       display_twilio_api_secret = NULL,
       display_openai_api_key = NULL;
