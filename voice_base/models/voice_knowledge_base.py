# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class VoiceKnowledgeBase(models.Model):
    """
    Knowledge base abstraction for RAG (Retrieval-Augmented Generation).

    This model provides a provider-agnostic interface for knowledge bases.
    Provider-specific implementations will handle the actual document storage
    and retrieval mechanisms.
    """
    _name = 'voice.knowledge.base'
    _description = 'Voice AI Knowledge Base'
    _order = 'sequence, name'

    # === Identity ===
    name = fields.Char(
        string='Name',
        required=True,
        help='Name of this knowledge base'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Whether this knowledge base is active'
    )
    description = fields.Text(
        string='Description',
        help='Description of what information this knowledge base contains'
    )

    # === Provider Link ===
    voice_provider_id = fields.Many2one(
        comodel_name='voice.provider',
        string='Provider',
        required=False,
        ondelete='restrict',
        help='Voice provider that manages this knowledge base (if provider-specific)'
    )

    # === Configuration ===
    external_kb_id = fields.Char(
        string='External KB ID',
        readonly=True,
        help='ID of the knowledge base in the external provider system'
    )
    chunk_size = fields.Integer(
        string='Chunk Size',
        default=1000,
        help='Size of text chunks for embedding (in characters)'
    )
    chunk_overlap = fields.Integer(
        string='Chunk Overlap',
        default=200,
        help='Overlap between chunks (in characters)'
    )
    top_k = fields.Integer(
        string='Top K Results',
        default=5,
        help='Number of most relevant chunks to retrieve'
    )
    similarity_threshold = fields.Float(
        string='Similarity Threshold',
        default=0.7,
        help='Minimum similarity score to include a chunk (0.0-1.0)'
    )

    # === Stats ===
    document_count = fields.Integer(
        string='Documents',
        default=0,
        readonly=True,
        help='Number of documents in this knowledge base'
    )
    total_chunks = fields.Integer(
        string='Total Chunks',
        default=0,
        readonly=True,
        help='Total number of embedded chunks'
    )
    total_tokens = fields.Integer(
        string='Total Tokens',
        default=0,
        readonly=True,
        help='Total tokens across all documents'
    )
    last_indexed = fields.Datetime(
        string='Last Indexed',
        readonly=True,
        help='When documents were last indexed/embedded'
    )

    # === Usage Stats ===
    query_count = fields.Integer(
        string='Query Count',
        default=0,
        readonly=True,
        help='Number of times this knowledge base has been queried'
    )
    last_queried = fields.Datetime(
        string='Last Queried',
        readonly=True,
        help='When this knowledge base was last queried'
    )

    def action_reindex(self):
        """Reindex all documents in the knowledge base."""
        self.ensure_one()
        # Implementation would be provider-specific
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Reindex Started'),
                'message': _('Reindexing not yet implemented'),
                'type': 'info',
                'sticky': False,
            }
        }

    def query(self, query_text, top_k=None, threshold=None):
        """
        Query the knowledge base for relevant information.

        Args:
            query_text (str): The query text
            top_k (int, optional): Number of results to return
            threshold (float, optional): Minimum similarity threshold

        Returns:
            list: List of relevant chunks with metadata
        """
        self.ensure_one()
        # This would be implemented by provider-specific modules
        raise NotImplementedError(_('Knowledge base query not yet implemented'))

    def add_document(self, content, metadata=None):
        """
        Add a document to the knowledge base.

        Args:
            content (str): Document content
            metadata (dict, optional): Document metadata

        Returns:
            dict: {'success': bool, 'document_id': str, 'chunks': int}
        """
        self.ensure_one()
        # This would be implemented by provider-specific modules
        raise NotImplementedError(_('Adding documents not yet implemented'))

    def remove_document(self, document_id):
        """
        Remove a document from the knowledge base.

        Args:
            document_id (str): ID of document to remove

        Returns:
            bool: True if successful
        """
        self.ensure_one()
        # This would be implemented by provider-specific modules
        raise NotImplementedError(_('Removing documents not yet implemented'))
