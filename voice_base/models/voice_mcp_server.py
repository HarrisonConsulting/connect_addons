# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .constants import MCP_TRANSPORT_LIST, MCP_AUTH_TYPE_LIST, MCP_APPROVAL_MODE_LIST


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
    approval_mode = fields.Selection(
        selection=MCP_APPROVAL_MODE_LIST,
        string='Approval Mode',
        required=True,
        default='auto',
        help='How to handle tool approvals: auto (all approved), whitelist (only allowed), manual (ask each time)'
    )
    allowed_tools = fields.Text(
        string='Allowed Tools',
        help='Comma-separated list of allowed tool names (for whitelist mode)'
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

    @api.constrains('approval_mode', 'allowed_tools')
    def _check_whitelist(self):
        """Validate whitelist configuration."""
        for server in self:
            if server.approval_mode == 'whitelist' and not server.allowed_tools:
                raise ValidationError(_(
                    'Whitelist approval mode requires allowed tools to be specified.'
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
        Check if a tool is allowed based on approval mode.

        Args:
            tool_name (str): Name of the tool to check

        Returns:
            bool: True if tool is allowed, False otherwise
        """
        self.ensure_one()

        if self.approval_mode == 'auto':
            return True
        elif self.approval_mode == 'whitelist':
            if not self.allowed_tools:
                return False
            allowed = [t.strip() for t in self.allowed_tools.split(',')]
            return tool_name in allowed
        elif self.approval_mode == 'manual':
            # Manual approval would need to be handled by the calling code
            return False

        return False
