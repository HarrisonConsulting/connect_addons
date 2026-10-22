"""Settings for the opt-in paid identity sources."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    caller_intel_google_places_api_key = fields.Char(
        string='Google Places API key',
        config_parameter='connect_caller_intel.google_places_api_key',
        help='API key for Google Places. It is stored as a system parameter, never on the source record.')
    caller_intel_telnyx_api_key = fields.Char(
        string='Telnyx API key',
        config_parameter='connect_caller_intel.telnyx_api_key',
        help='API key for Telnyx. It is stored as a system parameter, never on the source record.')
    caller_intel_ipqs_api_key = fields.Char(
        string='IPQualityScore API key',
        config_parameter='connect_caller_intel.ipqs_api_key',
        help='API key for IPQualityScore. It is stored as a system parameter, never on the source record.')
    caller_intel_trestle_api_key = fields.Char(
        string='Trestle API key',
        config_parameter='connect_caller_intel.trestle_api_key',
        help='API key for Trestle. It is stored as a system parameter, never on the source record.')
    caller_intel_ipqs_mark_from = fields.Integer(
        string='IPQS spam report from',
        config_parameter='connect_caller_intel.ipqs_mark_from', default=85,
        help='IPQS fraud score from which a spam report is recorded.')
