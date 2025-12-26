# -*- coding: utf-8 -*-
"""Tests for voice.agent.mixin model."""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVoiceAgentMixin(TransactionCase):
    """Test voice.agent.mixin functionality."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Check if voice models are available
        cls.has_voice_models = 'voice.agent.mixin' in cls.env

    def test_mixin_exists(self):
        """Test that voice.agent.mixin model exists."""
        if not self.has_voice_models:
            self.skipTest("Voice models not available")

        # The mixin should be an abstract model
        mixin = self.env['voice.agent.mixin']
        self.assertIsNotNone(mixin)


@tagged('post_install', '-at_install')
class TestVoiceTool(TransactionCase):
    """Test voice.tool model."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.has_voice_models = 'voice.tool' in cls.env

    def test_tool_create(self):
        """Test basic voice tool creation."""
        if not self.has_voice_models:
            self.skipTest("Voice models not available")

        tool = self.env['voice.tool'].create({
            'name': 'Test Tool',
            'description': 'A test tool for testing',
            'type': 'webhook',
        })
        self.assertTrue(tool.id)
        self.assertEqual(tool.name, 'Test Tool')

    def test_tool_types(self):
        """Test different tool types."""
        if not self.has_voice_models:
            self.skipTest("Voice models not available")

        # Get available tool types from selection field
        tool_model = self.env['voice.tool']
        type_field = tool_model._fields.get('type')
        if type_field and hasattr(type_field, 'selection'):
            self.assertIsNotNone(type_field.selection)


@tagged('post_install', '-at_install')
class TestVoiceTTSFile(TransactionCase):
    """Test voice.tts.file model."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.has_voice_models = 'voice.tts.file' in cls.env

    def test_tts_file_create(self):
        """Test basic TTS file creation."""
        if not self.has_voice_models:
            self.skipTest("Voice models not available")

        tts_file = self.env['voice.tts.file'].create({
            'name': 'Test TTS File',
            'text': 'Hello, this is a test message.',
        })
        self.assertTrue(tts_file.id)
        self.assertEqual(tts_file.name, 'Test TTS File')
