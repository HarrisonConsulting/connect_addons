# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .constants import (
    TOOL_TYPE_LIST, SYSTEM_TOOL_TYPE_LIST, HTTP_METHOD_LIST,
    WEBHOOK_AUTH_TYPE_LIST, OAUTH2_JWT_ALGORITHM_LIST,
    VOICEMAIL_ACTION_LIST, TRANSFER_DESTINATION_TYPE_LIST,
    TOOL_EXECUTION_SOUND_LIST, TOOL_SOUND_BEHAVIOR_LIST,
)


class VoiceTool(models.Model):
    """
    Provider-agnostic tool definition for voice agents.

    Tools can be:
    - Webhook: HTTP calls to external services
    - Client: Client-side actions (e.g., transfer, hang up)
    - System: Provider-specific system functions
    """
    _name = 'voice.tool'
    _description = 'Voice AI Tool'
    _order = 'sequence, name'

    # === Identity ===
    name = fields.Char(
        string='Name',
        required=True,
        help='Tool name (used by LLM to identify the tool)'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Whether this tool is available for use'
    )
    description = fields.Text(
        string='Description',
        required=True,
        help='Description of what this tool does (shown to LLM for tool selection)'
    )

    # === Tool Type ===
    tool_type = fields.Selection(
        selection=TOOL_TYPE_LIST,
        string='Tool Type',
        required=True,
        default='webhook',
        help='Type of tool: webhook (HTTP call), client (client-side action), or system (provider-specific)'
    )

    # === Webhook Configuration ===
    webhook_url = fields.Char(
        string='Webhook URL',
        help='URL to call for webhook tools'
    )
    webhook_method = fields.Selection(
        selection=HTTP_METHOD_LIST,
        string='HTTP Method',
        default='POST',
        help='HTTP method to use for webhook calls'
    )
    webhook_headers = fields.Text(
        string='HTTP Headers',
        help='JSON object of HTTP headers to send (e.g., {"Authorization": "Bearer token"})'
    )
    timeout_seconds = fields.Integer(
        string='Timeout (seconds)',
        default=30,
        help='How long to wait for webhook response before timing out'
    )

    # === Client Configuration ===
    wait_for_response = fields.Boolean(
        string='Wait for Response',
        default=True,
        help='For client tools: whether to wait for client-side execution before continuing'
    )
    client_timeout_seconds = fields.Integer(
        string='Client Timeout (seconds)',
        default=10,
        help='How long to wait for client-side tool execution'
    )

    # === System Tool Configuration ===
    system_tool_type = fields.Selection(
        selection=SYSTEM_TOOL_TYPE_LIST,
        string='System Tool Type',
        help='Type of system tool (end_call, language_detection, etc.)'
    )

    # === Webhook Authentication ===
    webhook_auth_type = fields.Selection(
        selection=WEBHOOK_AUTH_TYPE_LIST,
        string='Authentication Type',
        default='none',
        help='Authentication method for webhook calls'
    )
    webhook_auth_token = fields.Char(
        string='Auth Token/Password',
        groups='base.group_system',
        help='Bearer token, password (for Basic), or client secret (for OAuth2)'
    )
    webhook_auth_username = fields.Char(
        string='Username/Client ID',
        help='Username for Basic auth or Client ID for OAuth2'
    )
    webhook_oauth2_token_url = fields.Char(
        string='OAuth2 Token URL',
        help='Token endpoint URL for OAuth2 authentication'
    )
    webhook_oauth2_scopes = fields.Char(
        string='OAuth2 Scopes',
        help='Comma-separated list of OAuth2 scopes'
    )
    webhook_oauth2_extra_params = fields.Text(
        string='OAuth2 Extra Parameters',
        help='Additional JSON parameters for OAuth2 token request'
    )
    webhook_jwt_secret = fields.Char(
        string='JWT Secret',
        groups='base.group_system',
        help='Secret key for signing JWT tokens'
    )
    webhook_jwt_algorithm = fields.Selection(
        selection=OAUTH2_JWT_ALGORITHM_LIST,
        string='JWT Algorithm',
        default='HS256',
        help='Algorithm for JWT signing'
    )
    webhook_jwt_issuer = fields.Char(
        string='JWT Issuer',
        help='Issuer claim for JWT'
    )
    webhook_jwt_audience = fields.Char(
        string='JWT Audience',
        help='Audience claim for JWT'
    )
    webhook_jwt_subject = fields.Char(
        string='JWT Subject',
        help='Subject claim for JWT'
    )

    # === Pre-Tool Speech Configuration ===
    pre_tool_message = fields.Text(
        string='Pre-Tool Message',
        help='Message the agent speaks before executing this tool (e.g., "Let me check that for you...")'
    )
    force_pre_tool_message = fields.Boolean(
        string='Force Pre-Tool Message',
        default=False,
        help='Always speak the pre-tool message before execution'
    )

    # === Tool Execution Sound ===
    execution_sound = fields.Selection(
        selection=TOOL_EXECUTION_SOUND_LIST,
        string='Execution Sound',
        default='none',
        help='Ambient sound to play while tool is executing'
    )
    execution_sound_behavior = fields.Selection(
        selection=TOOL_SOUND_BEHAVIOR_LIST,
        string='Sound Behavior',
        default='auto',
        help='When to play the execution sound'
    )

    # === Transfer Call Configuration (for transfer_call system tool) ===
    transfer_destination_type = fields.Selection(
        selection=TRANSFER_DESTINATION_TYPE_LIST,
        string='Transfer To',
        default='phone',
        help='Type of destination for call transfer'
    )
    transfer_destination = fields.Char(
        string='Transfer Destination',
        help='Phone number (E.164), agent ID, or queue name depending on destination type'
    )
    transfer_message_caller = fields.Text(
        string='Caller Message',
        help='Message played to caller before transfer'
    )
    transfer_message_recipient = fields.Text(
        string='Recipient Message',
        help='Message played to recipient (human/agent) when they answer'
    )

    # === Voicemail Detection Configuration (for detect_voicemail system tool) ===
    voicemail_action = fields.Selection(
        selection=VOICEMAIL_ACTION_LIST,
        string='Voicemail Action',
        default='end_call',
        help='Action to take when voicemail is detected'
    )
    voicemail_message = fields.Text(
        string='Voicemail Message',
        help='Message to leave when voicemail is detected (supports dynamic variables)'
    )

    # === Play Tones Configuration (for play_tones system tool) ===
    tone_sequence = fields.Char(
        string='Tone Sequence',
        help='Tones to play (DTMF: 0-9, *, #, w=0.5s pause, W=1s pause)'
    )
    tone_out_of_band = fields.Boolean(
        string='Out-of-Band Signaling',
        default=False,
        help='Use out-of-band signaling for better telephony compatibility'
    )

    # === Parameters ===
    parameter_ids = fields.One2many(
        comodel_name='voice.tool.parameter',
        inverse_name='tool_id',
        string='Parameters',
        help='Parameters that can be passed to this tool'
    )
    parameter_count = fields.Integer(
        string='Parameter Count',
        compute='_compute_parameter_count',
        help='Number of parameters'
    )

    # === Stats ===
    usage_count = fields.Integer(
        string='Usage Count',
        default=0,
        readonly=True,
        help='How many times this tool has been called'
    )
    last_used = fields.Datetime(
        string='Last Used',
        readonly=True,
        help='When this tool was last called'
    )
    error_count = fields.Integer(
        string='Error Count',
        default=0,
        readonly=True,
        help='Number of errors encountered'
    )

    @api.depends('parameter_ids')
    def _compute_parameter_count(self):
        """Compute parameter count."""
        for tool in self:
            tool.parameter_count = len(tool.parameter_ids)

    @api.constrains('tool_type', 'webhook_url', 'system_tool_type', 'transfer_destination')
    def _check_tool_configuration(self):
        """Validate tool configuration based on type."""
        for tool in self:
            if tool.tool_type == 'webhook' and not tool.webhook_url:
                raise ValidationError(_('Webhook tools must have a URL configured.'))
            if tool.tool_type == 'system' and not tool.system_tool_type:
                raise ValidationError(_('System tools must have a system tool type selected.'))

            # Validate system tool specific requirements
            if tool.tool_type == 'system' and tool.system_tool_type:
                if tool.system_tool_type == 'transfer_call' and not tool.transfer_destination:
                    raise ValidationError(_(
                        'Transfer Call tools must have a transfer destination configured.'
                    ))

    @api.constrains('webhook_auth_type', 'webhook_auth_token', 'webhook_auth_username',
                    'webhook_oauth2_token_url', 'webhook_jwt_secret')
    def _check_webhook_auth_configuration(self):
        """Validate webhook authentication configuration."""
        for tool in self:
            if tool.tool_type != 'webhook':
                continue

            if tool.webhook_auth_type == 'bearer' and not tool.webhook_auth_token:
                raise ValidationError(_('Bearer authentication requires a token.'))
            if tool.webhook_auth_type == 'basic':
                if not tool.webhook_auth_username or not tool.webhook_auth_token:
                    raise ValidationError(_('Basic authentication requires username and password.'))
            if tool.webhook_auth_type == 'oauth2_client_credentials':
                if not tool.webhook_auth_username or not tool.webhook_auth_token or not tool.webhook_oauth2_token_url:
                    raise ValidationError(_(
                        'OAuth2 Client Credentials requires Client ID, Client Secret, and Token URL.'
                    ))
            if tool.webhook_auth_type == 'oauth2_jwt':
                if not tool.webhook_jwt_secret or not tool.webhook_oauth2_token_url:
                    raise ValidationError(_(
                        'OAuth2 JWT requires JWT Secret and Token URL.'
                    ))

    def action_test_tool(self):
        """Action to test tool execution."""
        self.ensure_one()
        # Implementation would depend on tool type
        # For now, just show a notification
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Tool Test'),
                'message': _('Tool testing not yet implemented'),
                'type': 'info',
                'sticky': False,
            }
        }

    def get_tool_schema(self):
        """
        Get OpenAI-compatible tool schema for this tool.

        Returns:
            dict: Tool schema in OpenAI function calling format
        """
        self.ensure_one()
        schema = {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'required': [],
                }
            }
        }

        for param in self.parameter_ids:
            schema['function']['parameters']['properties'][param.name] = {
                'type': param.parameter_type,
                'description': param.description,
            }
            if param.enum_values:
                schema['function']['parameters']['properties'][param.name]['enum'] = \
                    param.enum_values.split(',')
            if param.required:
                schema['function']['parameters']['required'].append(param.name)

        return schema


class VoiceToolParameter(models.Model):
    """
    Parameters for voice tools.

    Defines the input schema for tools using JSON Schema-like structure.
    """
    _name = 'voice.tool.parameter'
    _description = 'Voice Tool Parameter'
    _order = 'sequence, name'

    # === Identity ===
    tool_id = fields.Many2one(
        comodel_name='voice.tool',
        string='Tool',
        required=True,
        ondelete='cascade',
        help='The tool this parameter belongs to'
    )
    name = fields.Char(
        string='Name',
        required=True,
        help='Parameter name (used in API calls)'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order'
    )
    description = fields.Text(
        string='Description',
        required=True,
        help='Description of what this parameter does'
    )

    # === Type ===
    parameter_type = fields.Selection(
        selection=[
            ('string', 'String'),
            ('number', 'Number'),
            ('integer', 'Integer'),
            ('boolean', 'Boolean'),
            ('array', 'Array'),
            ('object', 'Object'),
        ],
        string='Type',
        required=True,
        default='string',
        help='Data type of this parameter'
    )

    # === Constraints ===
    required = fields.Boolean(
        string='Required',
        default=False,
        help='Whether this parameter is required'
    )
    default_value = fields.Char(
        string='Default Value',
        help='Default value if not provided'
    )
    enum_values = fields.Char(
        string='Enum Values',
        help='Comma-separated list of allowed values (for enum types)'
    )

    # === Validation ===
    min_value = fields.Float(
        string='Minimum Value',
        help='Minimum value for number/integer types'
    )
    max_value = fields.Float(
        string='Maximum Value',
        help='Maximum value for number/integer types'
    )
    pattern = fields.Char(
        string='Pattern',
        help='Regex pattern for string validation'
    )

    _sql_constraints = [
        ('unique_tool_param', 'unique(tool_id, name)',
         'Parameter name must be unique per tool!')
    ]
