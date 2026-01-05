# -*- coding: utf-8 -*-
"""
Migration Wizard for Legacy ElevenLabs Integration.

Provides a wizard-based migration path from the connect_elevenlabs module
ecosystem to the new voice_base/elevenlabs_provider architecture.

Migration handles:
- connect.elevenlabs_agent -> connect.voice.agent (via voice.agent.mixin)
- connect.elevenlabs_voice -> voice.voice
- connect.elevenlabs_file -> voice.tts.file
- connect.elevenlabs_system_message -> voice.system.message
- connect.elevenlabs_agent_tool -> voice.tool
- connect.elevenlabs_phone_registration -> elevenlabs.phone.registration
"""
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


class ElevenLabsMigrationWizard(models.TransientModel):
    """
    Wizard for migrating from legacy connect_elevenlabs to new architecture.
    """
    _name = 'elevenlabs.migration.wizard'
    _description = 'ElevenLabs Migration Wizard'

    # === State ===
    state = fields.Selection([
        ('check', 'Check Installation'),
        ('preview', 'Preview Migration'),
        ('migrate', 'Migrate Data'),
        ('complete', 'Complete'),
    ], default='check', string='State')

    # === Detection Results ===
    legacy_installed = fields.Boolean(
        string='Legacy Module Installed',
        readonly=True,
        help='Whether connect_elevenlabs is currently installed'
    )
    legacy_version = fields.Char(
        string='Legacy Version',
        readonly=True,
        help='Installed version of connect_elevenlabs'
    )

    # === Record Counts ===
    agent_count = fields.Integer(
        string='Agents to Migrate',
        readonly=True,
        help='Number of ElevenLabs agents to migrate'
    )
    voice_count = fields.Integer(
        string='Voices to Migrate',
        readonly=True,
        help='Number of voice records to migrate'
    )
    file_count = fields.Integer(
        string='TTS Files to Migrate',
        readonly=True,
        help='Number of TTS files to migrate'
    )
    tool_count = fields.Integer(
        string='Tools to Migrate',
        readonly=True,
        help='Number of agent tools to migrate'
    )
    phone_count = fields.Integer(
        string='Phone Registrations to Migrate',
        readonly=True,
        help='Number of phone registrations to migrate'
    )

    # === Options ===
    migrate_voices = fields.Boolean(
        string='Migrate Voices',
        default=True,
        help='Migrate voice library records'
    )
    migrate_files = fields.Boolean(
        string='Migrate TTS Files',
        default=True,
        help='Migrate generated TTS audio files'
    )
    migrate_tools = fields.Boolean(
        string='Migrate Tools',
        default=True,
        help='Migrate agent tool configurations'
    )
    preserve_legacy = fields.Boolean(
        string='Preserve Legacy Records',
        default=True,
        help='Keep legacy records after migration (recommended for rollback)'
    )

    # === Results ===
    migration_log = fields.Text(
        string='Migration Log',
        readonly=True,
        help='Detailed log of migration actions'
    )
    agents_migrated = fields.Integer(
        string='Agents Migrated',
        readonly=True,
        help='Number of agents successfully migrated'
    )
    errors = fields.Text(
        string='Errors',
        readonly=True,
        help='Any errors encountered during migration'
    )

    @api.model
    def default_get(self, fields_list):
        """Load detection results on wizard open."""
        res = super().default_get(fields_list)
        res.update(self._detect_legacy_installation())
        return res

    def _detect_legacy_installation(self):
        """
        Detect legacy connect_elevenlabs installation.

        Returns:
            dict: Detection results
        """
        Provider = self.env['voice.provider']
        summary = Provider.get_migration_summary()

        legacy_installed = bool(summary.get('legacy_installed'))
        legacy_version = summary.get('legacy_installed', {}).get('connect_elevenlabs', '')

        # Get record counts if legacy is installed
        agent_count = 0
        voice_count = 0
        file_count = 0
        tool_count = 0
        phone_count = 0

        if legacy_installed:
            legacy_models = summary.get('legacy_models', {})
            agent_count = legacy_models.get('connect.elevenlabs_agent', 0)
            voice_count = legacy_models.get('connect.elevenlabs_voice', 0)
            file_count = legacy_models.get('connect.elevenlabs_file', 0)
            tool_count = legacy_models.get('connect.elevenlabs_agent_tool', 0)
            phone_count = legacy_models.get('connect.elevenlabs_phone_registration', 0)

        return {
            'legacy_installed': legacy_installed,
            'legacy_version': legacy_version,
            'agent_count': agent_count,
            'voice_count': voice_count,
            'file_count': file_count,
            'tool_count': tool_count,
            'phone_count': phone_count,
        }

    def action_check(self):
        """Check installation and move to preview state."""
        self.ensure_one()
        self.write(self._detect_legacy_installation())
        self.state = 'preview'
        return self._reopen_wizard()

    def action_preview(self):
        """Preview migration and confirm."""
        self.ensure_one()
        if not self.legacy_installed:
            raise UserError(_('No legacy installation detected. Nothing to migrate.'))
        self.state = 'migrate'
        return self._reopen_wizard()

    def action_migrate(self):
        """Execute the migration."""
        self.ensure_one()
        if not self.legacy_installed:
            raise UserError(_('No legacy installation detected.'))

        log_lines = []
        errors = []
        agents_migrated = 0

        try:
            # Get or create the ElevenLabs provider
            provider = self._get_or_create_provider()
            log_lines.append(f'Using provider: {provider.name} (ID: {provider.id})')

            # Migrate voices first (agents depend on them)
            if self.migrate_voices and self.voice_count > 0:
                voice_result = self._migrate_voices(provider)
                log_lines.append(f'Migrated {voice_result["count"]} voices')
                if voice_result.get('errors'):
                    errors.extend(voice_result['errors'])

            # Migrate TTS files
            if self.migrate_files and self.file_count > 0:
                file_result = self._migrate_tts_files(provider)
                log_lines.append(f'Migrated {file_result["count"]} TTS files')
                if file_result.get('errors'):
                    errors.extend(file_result['errors'])

            # Migrate tools
            if self.migrate_tools and self.tool_count > 0:
                tool_result = self._migrate_tools()
                log_lines.append(f'Migrated {tool_result["count"]} tools')
                if tool_result.get('errors'):
                    errors.extend(tool_result['errors'])

            # Migrate agents
            if self.agent_count > 0:
                agent_result = self._migrate_agents(provider)
                agents_migrated = agent_result['count']
                log_lines.append(f'Migrated {agents_migrated} agents')
                if agent_result.get('errors'):
                    errors.extend(agent_result['errors'])

            # Migrate phone registrations
            if self.phone_count > 0:
                phone_result = self._migrate_phone_registrations(provider)
                log_lines.append(f'Migrated {phone_result["count"]} phone registrations')
                if phone_result.get('errors'):
                    errors.extend(phone_result['errors'])

            log_lines.append('\nMigration completed successfully!')

        except Exception as e:
            logger.exception('Migration failed: %s', e)
            errors.append(f'Migration failed: {e}')
            log_lines.append(f'\nMigration FAILED: {e}')

        self.write({
            'state': 'complete',
            'migration_log': '\n'.join(log_lines),
            'agents_migrated': agents_migrated,
            'errors': '\n'.join(errors) if errors else False,
        })

        return self._reopen_wizard()

    def _get_or_create_provider(self):
        """Get existing or create new ElevenLabs provider."""
        Provider = self.env['voice.provider.elevenlabs']
        provider = Provider.search([('provider_type', '=', 'elevenlabs')], limit=1)

        if not provider:
            # Try to get API key from legacy settings
            api_key = self._get_legacy_api_key()
            provider = Provider.create({
                'name': 'ElevenLabs (Migrated)',
                'provider_type': 'elevenlabs',
                'api_key': api_key,
                'is_default': True,
            })
            logger.info('Created new ElevenLabs provider: %s', provider.id)

        return provider

    def _get_legacy_api_key(self):
        """Get API key from legacy connect.settings."""
        try:
            Settings = self.env['connect.settings']
            return Settings.sudo().get_param('elevenlabs_api_key') or ''
        except Exception:
            return ''

    def _migrate_voices(self, provider):
        """Migrate voice records from legacy to new model."""
        result = {'count': 0, 'errors': []}

        try:
            LegacyVoice = self.env['connect.elevenlabs_voice']
            NewVoice = self.env['voice.voice']

            for legacy in LegacyVoice.sudo().search([]):
                try:
                    # Check if already migrated
                    existing = NewVoice.search([
                        ('external_voice_id', '=', legacy.voice_id),
                        ('voice_provider_id', '=', provider.id),
                    ], limit=1)

                    if not existing:
                        NewVoice.create({
                            'name': legacy.name,
                            'voice_provider_id': provider.id,
                            'external_voice_id': legacy.voice_id,
                            'preview_url': getattr(legacy, 'preview_url', False),
                        })
                        result['count'] += 1

                except Exception as e:
                    result['errors'].append(f'Voice {legacy.name}: {e}')

        except KeyError:
            result['errors'].append('Legacy voice model not found')

        return result

    def _migrate_tts_files(self, provider):
        """Migrate TTS files from legacy to new model."""
        result = {'count': 0, 'errors': []}

        try:
            LegacyFile = self.env['connect.elevenlabs_file']
            NewFile = self.env['voice.tts.file']

            for legacy in LegacyFile.sudo().search([]):
                try:
                    # Check if already migrated
                    existing = NewFile.search([
                        ('legacy_model', '=', 'connect.elevenlabs_file'),
                        ('legacy_id', '=', legacy.id),
                    ], limit=1)

                    if not existing:
                        NewFile.create({
                            'text': legacy.text,
                            'audio_file': legacy.file,
                            'audio_filename': legacy.filename,
                            'voice_provider_id': provider.id,
                            'legacy_model': 'connect.elevenlabs_file',
                            'legacy_id': legacy.id,
                        })
                        result['count'] += 1

                except Exception as e:
                    result['errors'].append(f'TTS file {legacy.id}: {e}')

        except KeyError:
            result['errors'].append('Legacy TTS file model not found')

        return result

    def _migrate_tools(self):
        """Migrate agent tools from legacy to new model."""
        result = {'count': 0, 'errors': []}

        try:
            LegacyTool = self.env['connect.elevenlabs_agent_tool']
            NewTool = self.env['voice.tool']

            for legacy in LegacyTool.sudo().search([]):
                try:
                    # Map tool type
                    tool_type_map = {
                        'webhook': 'webhook',
                        'client': 'client',
                        'system': 'system',
                    }
                    tool_type = tool_type_map.get(legacy.tool_type, 'webhook')

                    # Check if already exists
                    existing = NewTool.search([
                        ('name', '=', legacy.name),
                    ], limit=1)

                    if not existing:
                        NewTool.create({
                            'name': legacy.name,
                            'description': getattr(legacy, 'description', ''),
                            'tool_type': tool_type,
                        })
                        result['count'] += 1

                except Exception as e:
                    result['errors'].append(f'Tool {legacy.name}: {e}')

        except KeyError:
            result['errors'].append('Legacy tool model not found')

        return result

    def _migrate_agents(self, provider):
        """Migrate agent configurations from legacy to connect.voice.agent."""
        result = {'count': 0, 'errors': []}

        try:
            LegacyAgent = self.env['connect.elevenlabs_agent']
            NewAgent = self.env['connect.voice.agent']

            for legacy in LegacyAgent.sudo().search([]):
                try:
                    # Check if already migrated (by external agent_uid)
                    existing = NewAgent.search([
                        ('external_agent_id', '=', legacy.agent_uid),
                    ], limit=1)

                    if not existing:
                        # Find matching voice
                        voice = self.env['voice.voice'].search([
                            ('external_voice_id', '=', legacy.voice_id.voice_id if legacy.voice_id else ''),
                            ('voice_provider_id', '=', provider.id),
                        ], limit=1)

                        vals = {
                            'name': legacy.name,
                            'voice_provider_id': provider.id,
                            'external_agent_id': legacy.agent_uid,
                            'voice_id': voice.id if voice else False,
                            'system_prompt': legacy.system_prompt,
                            'first_message': legacy.first_message,
                            'llm_model': legacy.llm or 'gpt-4o',
                            'tts_model': legacy.tts_model or 'eleven_flash_v2_5',
                            'language': legacy.language or 'en',
                            'temperature': legacy.temperature or 1.0,
                        }

                        # Add extension/callflow if present
                        if hasattr(legacy, 'exten') and legacy.exten:
                            vals['exten_id'] = legacy.exten.id
                        if hasattr(legacy, 'callflow_id') and legacy.callflow_id:
                            vals['callflow_id'] = legacy.callflow_id.id

                        NewAgent.create(vals)
                        result['count'] += 1

                except Exception as e:
                    result['errors'].append(f'Agent {legacy.name}: {e}')

        except KeyError:
            result['errors'].append('Legacy agent model not found')

        return result

    def _migrate_phone_registrations(self, provider):
        """Migrate phone registrations from legacy to new model."""
        result = {'count': 0, 'errors': []}

        try:
            LegacyPhone = self.env['connect.elevenlabs_phone_registration']
            NewPhone = self.env['elevenlabs.phone.registration']

            for legacy in LegacyPhone.sudo().search([]):
                try:
                    # Check if already migrated
                    existing = NewPhone.search([
                        ('phone_number_id', '=', legacy.phone_number_id),
                    ], limit=1)

                    if not existing:
                        NewPhone.create({
                            'provider_id': provider.id,
                            'phone_number': legacy.phone_number,
                            'phone_number_id': legacy.phone_number_id,
                            'label': getattr(legacy, 'label', legacy.phone_number),
                        })
                        result['count'] += 1

                except Exception as e:
                    result['errors'].append(f'Phone {legacy.phone_number}: {e}')

        except KeyError:
            result['errors'].append('Legacy phone registration model not found')

        return result

    def _reopen_wizard(self):
        """Return action to reopen wizard at current state."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_close(self):
        """Close the wizard."""
        return {'type': 'ir.actions.act_window_close'}
