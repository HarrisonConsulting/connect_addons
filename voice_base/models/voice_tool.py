# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .constants import TOOL_TYPE_LIST, SYSTEM_TOOL_TYPE_LIST, HTTP_METHOD_LIST


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

    # === Parameters ===
    parameter_ids = fields.One2many(
        comodel_name='voice.tool.parameter',
        inverse_name='tool_id',
        string='Parameters',
        help='Parameters that can be passed to this tool'
    )
    parameter_count = fields.Integer(
        string='Parameters',
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

    @api.constrains('tool_type', 'webhook_url', 'system_tool_type')
    def _check_tool_configuration(self):
        """Validate tool configuration based on type."""
        for tool in self:
            if tool.tool_type == 'webhook' and not tool.webhook_url:
                raise ValidationError(_('Webhook tools must have a URL configured.'))
            if tool.tool_type == 'system' and not tool.system_tool_type:
                raise ValidationError(_('System tools must have a system tool type selected.'))

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
