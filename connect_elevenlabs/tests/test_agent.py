# -*- coding: utf-8 -*-
"""Tests for connect.elevenlabs_agent model."""

from odoo.tests import tagged
from odoo.exceptions import ValidationError
from .common import ElevenLabsTestCase


@tagged('post_install', '-at_install')
class TestElevenLabsAgent(ElevenLabsTestCase):
    """Test ElevenLabs agent CRUD operations."""

    def test_agent_create(self):
        """Test basic agent creation."""
        if not self.test_voice:
            self.skipTest("ElevenLabs voice model not available")

        agent = self.env['connect.elevenlabs_agent'].create({
            'name': 'Test Agent',
            'first_message': 'Hello, how can I help you?',
            'voice': self.test_voice.id,
        })
        self.assertTrue(agent.id)
        self.assertEqual(agent.name, 'Test Agent')

    def test_agent_name_required(self):
        """Test that agent name is required."""
        if not self.test_voice:
            self.skipTest("ElevenLabs voice model not available")

        with self.assertRaises(Exception):
            self.env['connect.elevenlabs_agent'].create({
                'voice': self.test_voice.id,
            })

    def test_agent_voice_required(self):
        """Test that agent voice is required."""
        with self.assertRaises(Exception):
            self.env['connect.elevenlabs_agent'].create({
                'name': 'Test Agent',
            })


@tagged('post_install', '-at_install')
class TestElevenLabsAgentConstraints(ElevenLabsTestCase):
    """Test ElevenLabs agent field constraints."""

    def test_temperature_constraint_valid(self):
        """Test valid temperature values (0-1)."""
        if not self.test_voice:
            self.skipTest("ElevenLabs voice model not available")

        # Valid temperatures
        for temp in [0.0, 0.5, 1.0]:
            agent = self.env['connect.elevenlabs_agent'].create({
                'name': f'Agent Temp {temp}',
                'voice': self.test_voice.id,
                'temperature': temp,
            })
            self.assertEqual(agent.temperature, temp)

    def test_speed_default(self):
        """Test default speed value."""
        if not self.test_voice:
            self.skipTest("ElevenLabs voice model not available")

        agent = self.env['connect.elevenlabs_agent'].create({
            'name': 'Test Agent',
            'voice': self.test_voice.id,
        })
        # Default speed should be set
        self.assertIsNotNone(agent.speed)
