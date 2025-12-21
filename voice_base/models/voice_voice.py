# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from .constants import AUDIO_FORMAT_LIST


class VoiceVoice(models.Model):
    """
    Voice library model for available TTS voices.

    Stores voice metadata synced from providers. Each provider can sync
    their available voices to this shared model.
    """
    _name = 'voice.voice'
    _description = 'Voice Library'
    _order = 'sequence, name'

    # === Identity ===
    name = fields.Char(
        string='Name',
        required=True,
        help='Display name of the voice'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Whether this voice is available for use'
    )
    description = fields.Text(
        string='Description',
        help='Description of the voice characteristics'
    )

    # === Provider Link ===
    voice_provider_id = fields.Many2one(
        comodel_name='voice.provider',
        string='Provider',
        required=True,
        ondelete='cascade',
        help='Voice provider that provides this voice'
    )
    external_voice_id = fields.Char(
        string='External Voice ID',
        required=True,
        help='Voice ID in the external provider system'
    )

    # === Voice Metadata ===
    gender = fields.Selection(
        selection=[
            ('male', 'Male'),
            ('female', 'Female'),
            ('neutral', 'Neutral'),
        ],
        string='Gender',
        help='Voice gender'
    )
    age = fields.Selection(
        selection=[
            ('young', 'Young'),
            ('middle_aged', 'Middle Aged'),
            ('old', 'Old'),
        ],
        string='Age',
        help='Approximate age of the voice'
    )
    accent = fields.Char(
        string='Accent',
        help='Voice accent (e.g., American, British, Australian)'
    )
    language_code = fields.Char(
        string='Language Code',
        help='Primary language code (e.g., en-US, es-ES)'
    )

    # === Voice Characteristics ===
    use_case = fields.Selection(
        selection=[
            ('general', 'General'),
            ('narration', 'Narration'),
            ('conversational', 'Conversational'),
            ('news', 'News'),
            ('customer_service', 'Customer Service'),
        ],
        string='Use Case',
        help='Recommended use case for this voice'
    )
    voice_style = fields.Char(
        string='Voice Style',
        help='Style tags (e.g., warm, professional, energetic)'
    )

    # === Technical ===
    sample_rate = fields.Integer(
        string='Sample Rate',
        help='Audio sample rate in Hz'
    )
    audio_format = fields.Selection(
        selection=AUDIO_FORMAT_LIST,
        string='Audio Format',
        help='Default audio format for this voice'
    )

    # === Cloning ===
    is_cloned = fields.Boolean(
        string='Cloned Voice',
        default=False,
        help='Whether this is a cloned/custom voice'
    )
    cloned_from_partner_id = fields.Many2one(
        comodel_name='res.partner',
        string='Cloned From',
        ondelete='set null',
        help='Contact whose voice was cloned'
    )

    # === Preview ===
    preview_url = fields.Char(
        string='Preview URL',
        help='URL to preview audio sample of this voice'
    )
    preview_text = fields.Char(
        string='Preview Text',
        help='Text used for preview samples'
    )

    # === Stats ===
    usage_count = fields.Integer(
        string='Usage Count',
        default=0,
        readonly=True,
        help='Number of times this voice has been used'
    )
    last_used = fields.Datetime(
        string='Last Used',
        readonly=True,
        help='When this voice was last used'
    )

    # === Sync ===
    last_synced = fields.Datetime(
        string='Last Synced',
        readonly=True,
        help='When this voice was last synced from provider'
    )

    _sql_constraints = [
        ('unique_provider_voice', 'unique(voice_provider_id, external_voice_id)',
         'Voice ID must be unique per provider!')
    ]

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to set last_synced."""
        now = fields.Datetime.now()
        for vals in vals_list:
            vals['last_synced'] = now
        return super().create(vals_list)

    def write(self, vals):
        """Override write to update last_synced."""
        vals['last_synced'] = fields.Datetime.now()
        return super().write(vals)

    def action_preview_voice(self):
        """Action to preview this voice."""
        self.ensure_one()
        if self.preview_url:
            return {
                'type': 'ir.actions.act_url',
                'url': self.preview_url,
                'target': 'new',
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Preview'),
                    'message': _('No preview available for this voice'),
                    'type': 'warning',
                    'sticky': False,
                }
            }

    def action_test_voice(self):
        """Action to test this voice with custom text."""
        self.ensure_one()
        # This would open a wizard to test TTS with custom text
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Test Voice'),
                'message': _('Voice testing not yet implemented'),
                'type': 'info',
                'sticky': False,
            }
        }

    @api.model
    def get_voices_by_provider(self, provider_id):
        """
        Get all voices for a specific provider.

        Args:
            provider_id (int): ID of the voice provider

        Returns:
            recordset: voice.voice records
        """
        return self.search([('voice_provider_id', '=', provider_id)])

    @api.model
    def find_voice(self, **criteria):
        """
        Find voices matching criteria.

        Args:
            **criteria: Search criteria (gender, accent, use_case, etc.)

        Returns:
            recordset: Matching voice.voice records
        """
        domain = [(key, '=', value) for key, value in criteria.items() if value]
        return self.search(domain)
