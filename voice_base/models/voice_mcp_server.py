# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .constants import (
    MCP_TRANSPORT_LIST, MCP_AUTH_TYPE_LIST, APPROVAL_POLICY_LIST,
    TOOL_EXECUTION_MODE_LIST, TOOL_EXECUTION_SOUND_LIST, TOOL_SOUND_BEHAVIOR_LIST,
    TOOL_APPROVAL_STATUS_LIST,
)


class VoiceMCPServer(models.Model):
    """
    Model Context Protocol (MCP) Server configuration.

    MCP servers provide additional capabilities to voice agents through
    a standardized protocol. This model stores the connection configuration
    and manages tool approval settings.
    """
    _name = 'voice.mcp.server'
    _description = 'MCP Server Configuration'
    _order = 'sequence, name'

    # === Identity ===
    name = fields.Char(
        string='Name',
        required=True,
        help='Display name for this MCP server'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Whether this MCP server is active'
    )
    description = fields.Text(
        string='Description',
        help='Description of what this MCP server provides'
    )

    # === Connection ===
    url = fields.Char(
        string='Server URL',
        required=True,
        help='URL of the MCP server'
    )
    transport = fields.Selection(
        selection=MCP_TRANSPORT_LIST,
        string='Transport',
        required=True,
        default='sse',
        help='Transport protocol: SSE (Server-Sent Events) or HTTP'
    )

    # === Authentication ===
    auth_type = fields.Selection(
        selection=MCP_AUTH_TYPE_LIST,
        string='Authentication Type',
        required=True,
        default='none',
        help='Type of authentication to use'
    )
    auth_token = fields.Char(
        string='Auth Token',
        groups='base.group_system',
        help='Authentication token (for Bearer or API Key auth)'
    )
    auth_header_name = fields.Char(
        string='Auth Header Name',
        default='Authorization',
        help='HTTP header name for authentication (default: Authorization)'
    )
    custom_headers = fields.Text(
        string='Custom Headers',
        help='Additional HTTP headers as JSON (e.g., {"X-Custom": "value"})'
    )

    # === Tool Approval ===
    approval_policy = fields.Selection(
        selection=APPROVAL_POLICY_LIST,
        string='Approval Policy',
        required=True,
        default='auto',
        help='How to handle tool approvals: auto (all approved), '
             'manual (ask each time), per_tool (fine-grained per tool)'
    )
    allowed_tools = fields.Text(
        string='Allowed Tools',
        help='Comma-separated list of auto-approved tool names (for per_tool mode)'
    )

    # === Tool Config Overrides ===
    tool_override_ids = fields.One2many(
        comodel_name='voice.mcp.tool.override',
        inverse_name='mcp_server_id',
        string='Tool Overrides',
        help='Per-tool configuration overrides'
    )

    # === Execution Settings ===
    execution_mode = fields.Selection(
        selection=TOOL_EXECUTION_MODE_LIST,
        string='Execution Mode',
        default='immediate',
        help='When to execute tool calls: immediate, after speech, or background'
    )
    force_pre_tool_message = fields.Boolean(
        string='Force Pre-Tool Message',
        default=False,
        help='Always speak before executing any tool from this server'
    )
    disable_interruptions = fields.Boolean(
        string='Disable Interruptions',
        default=False,
        help='Prevent user from interrupting during tool execution'
    )
    disable_compression = fields.Boolean(
        string='Disable Compression',
        default=False,
        help='Disable compression for MCP communication'
    )

    # === Tool Execution Sound ===
    execution_sound = fields.Selection(
        selection=TOOL_EXECUTION_SOUND_LIST,
        string='Execution Sound',
        default='none',
        help='Ambient sound to play while tools from this server are executing'
    )
    execution_sound_behavior = fields.Selection(
        selection=TOOL_SOUND_BEHAVIOR_LIST,
        string='Sound Behavior',
        default='auto',
        help='When to play the execution sound'
    )

    # === Provider Sync (generic - works with any provider) ===
    external_id = fields.Char(
        string='External ID',
        readonly=True,
        copy=False,
        help='MCP Server ID in the external provider (set after sync)'
    )
    sync_status = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('syncing', 'Syncing'),
            ('synced', 'Synced'),
            ('error', 'Error'),
        ],
        string='Sync Status',
        default='draft',
        readonly=True,
        help='Synchronization status with external provider'
    )
    sync_error = fields.Text(
        string='Sync Error',
        readonly=True,
        help='Last synchronization error message'
    )
    last_sync = fields.Datetime(
        string='Last Sync',
        readonly=True,
        help='When this server was last synced with the provider'
    )

    # === Health Monitoring ===
    last_health_check = fields.Datetime(
        string='Last Health Check',
        readonly=True,
        help='When the server health was last checked'
    )
    health_status = fields.Selection(
        selection=[
            ('unknown', 'Unknown'),
            ('healthy', 'Healthy'),
            ('degraded', 'Degraded'),
            ('down', 'Down'),
        ],
        string='Health Status',
        default='unknown',
        readonly=True,
        help='Current health status of the server'
    )
    health_message = fields.Text(
        string='Health Message',
        readonly=True,
        help='Last health check message or error'
    )

    # === Stats ===
    tool_count = fields.Integer(
        string='Available Tools',
        default=0,
        readonly=True,
        help='Number of tools provided by this server'
    )
    usage_count = fields.Integer(
        string='Usage Count',
        default=0,
        readonly=True,
        help='Number of times tools from this server have been used'
    )
    error_count = fields.Integer(
        string='Error Count',
        default=0,
        readonly=True,
        help='Number of errors encountered'
    )

    @api.constrains('url')
    def _check_url(self):
        """Validate URL format."""
        for server in self:
            if server.url and not server.url.startswith(('http://', 'https://')):
                raise ValidationError(_('Server URL must start with http:// or https://'))

    @api.constrains('approval_policy', 'allowed_tools')
    def _check_approval_config(self):
        """Validate approval configuration."""
        for server in self:
            # Per-tool approval requires either allowed_tools or tool_override_ids
            if server.approval_policy == 'per_tool':
                if not server.allowed_tools and not server.tool_override_ids:
                    raise ValidationError(_(
                        'Per-tool approval policy requires either allowed tools '
                        'or tool overrides to be configured.'
                    ))

    def action_health_check(self):
        """Perform health check on MCP server."""
        self.ensure_one()
        # This would be implemented by provider modules
        # For now, just update the timestamp
        self.write({
            'last_health_check': fields.Datetime.now(),
            'health_status': 'unknown',
            'health_message': _('Health check not yet implemented'),
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Health Check'),
                'message': _('Health check performed at %s', self.last_health_check),
                'type': 'info',
                'sticky': False,
            }
        }

    def action_sync_tools(self):
        """Sync available tools from MCP server."""
        self.ensure_one()
        # This would be implemented by provider modules
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Tool Sync'),
                'message': _('Tool synchronization not yet implemented'),
                'type': 'info',
                'sticky': False,
            }
        }

    def is_tool_allowed(self, tool_name):
        """
        Check if a tool is allowed based on approval policy.

        Args:
            tool_name (str): Name of the tool to check

        Returns:
            str: 'approved', 'requires_approval', or 'disabled'
        """
        self.ensure_one()

        if self.approval_policy == 'auto':
            return 'approved'
        elif self.approval_policy == 'manual':
            return 'requires_approval'
        elif self.approval_policy == 'per_tool':
            # Check tool overrides first
            override = self.tool_override_ids.filtered(
                lambda o: o.tool_name == tool_name
            )
            if override:
                return override[0].approval_status

            # Check allowed_tools list
            if self.allowed_tools:
                allowed = [t.strip() for t in self.allowed_tools.split(',')]
                if tool_name in allowed:
                    return 'approved'

            # Default to requires approval for unlisted tools
            return 'requires_approval'

        return 'disabled'

    def get_tool_config(self, tool_name):
        """
        Get configuration for a specific tool.

        Args:
            tool_name (str): Name of the tool

        Returns:
            dict: Tool configuration including approval status and any overrides
        """
        self.ensure_one()

        config = {
            'approval_status': self.is_tool_allowed(tool_name),
            'pre_tool_message': None,
            'force_pre_tool_message': self.force_pre_tool_message,
            'execution_sound': self.execution_sound if self.execution_sound != 'none' else None,
            'execution_sound_behavior': self.execution_sound_behavior,
        }

        # Apply tool-specific overrides
        override = self.tool_override_ids.filtered(
            lambda o: o.tool_name == tool_name
        )
        if override:
            override = override[0]
            if override.pre_tool_message:
                config['pre_tool_message'] = override.pre_tool_message
            if override.force_pre_tool_message:
                config['force_pre_tool_message'] = True
            if override.execution_sound and override.execution_sound != 'none':
                config['execution_sound'] = override.execution_sound
            if override.execution_sound_behavior:
                config['execution_sound_behavior'] = override.execution_sound_behavior

        return config


class VoiceMCPToolOverride(models.Model):
    """
    Per-tool configuration overrides for MCP servers.

    Allows fine-grained control over individual tools provided by an MCP server,
    including approval status, pre-tool speech, and sound effects.
    """
    _name = 'voice.mcp.tool.override'
    _description = 'MCP Tool Configuration Override'
    _order = 'sequence, name'

    # === Identity ===
    mcp_server_id = fields.Many2one(
        comodel_name='voice.mcp.server',
        string='MCP Server',
        required=True,
        ondelete='cascade',
        help='The MCP server this override belongs to'
    )
    tool_name = fields.Char(
        string='Tool Name',
        required=True,
        help='Name of the tool from the MCP server to configure'
    )
    name = fields.Char(
        string='Display Name',
        help='Friendly display name for this tool (optional)'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order'
    )

    # === Approval Status ===
    approval_status = fields.Selection(
        selection=[
            ('auto_approved', 'Auto-approved'),
            ('requires_approval', 'Requires Approval'),
            ('disabled', 'Disabled'),
        ],
        string='Approval Status',
        default='auto_approved',
        required=True,
        help='Whether this tool runs automatically, requires approval, or is disabled'
    )

    # === Pre-Tool Message ===
    pre_tool_message = fields.Text(
        string='Pre-Tool Message',
        help='Message the agent speaks before executing this specific tool'
    )
    force_pre_tool_message = fields.Boolean(
        string='Force Pre-Tool Message',
        default=False,
        help='Always speak the pre-tool message before execution'
    )

    # === Execution Sound ===
    execution_sound = fields.Selection(
        selection=TOOL_EXECUTION_SOUND_LIST,
        string='Execution Sound',
        default='none',
        help='Override sound to play while this tool is executing'
    )
    execution_sound_behavior = fields.Selection(
        selection=TOOL_SOUND_BEHAVIOR_LIST,
        string='Sound Behavior',
        help='Override sound behavior for this tool'
    )

    # === Additional Configuration ===
    description_override = fields.Text(
        string='Description Override',
        help='Override the tool description provided by the MCP server'
    )
    parameters_override = fields.Text(
        string='Parameters Override',
        help='JSON override for tool parameters schema'
    )

    _sql_constraints = [
        ('unique_mcp_tool', 'unique(mcp_server_id, tool_name)',
         'Tool name must be unique per MCP server!')
    ]
