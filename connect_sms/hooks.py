import logging

_logger = logging.getLogger(__name__)


def _post_init_hook(env):
    """On install, sync Connect credentials and numbers to sms_twilio models."""
    settings = env['connect.settings'].sudo()
    account_sid = settings.get_param('account_sid')
    auth_token = settings.get_param('auth_token')
    if account_sid and auth_token:
        companies = env['res.company'].sudo().search([])
        companies.write({
            'sms_provider': 'twilio',
            'sms_twilio_account_sid': account_sid,
            'sms_twilio_auth_token': auth_token,
        })
        _logger.info(
            'connect_sms: synced Twilio credentials to %d companies',
            len(companies),
        )
    else:
        _logger.warning(
            'connect_sms: no Twilio credentials in connect.settings, skipping sync'
        )
    # Sync phone numbers from connect.outgoing_callerid to sms.twilio.number
    env['connect.outgoing_callerid'].sudo()._sync_to_sms_twilio_numbers()
