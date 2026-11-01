# -*- coding: utf-8 -*-
"""Common test utilities for ElevenLabs integration tests."""

from contextlib import contextmanager
from unittest.mock import patch, MagicMock

from odoo.tests import tagged
from odoo.addons.connect.tests.common import ConnectTestCase


class MockElevenLabsAgent:
    """Mock ElevenLabs Agent object."""

    def __init__(self, agent_id, name='Test Agent', **kwargs):
        self.agent_id = agent_id
        self.name = name
        self.conversation_config = MagicMock()
        self.conversation_config.agent = MagicMock()
        self.conversation_config.agent.first_message = kwargs.get('first_message', 'Hello')
        self.conversation_config.agent.language = kwargs.get('language', 'en')
        self.conversation_config.agent.prompt = MagicMock()
        self.conversation_config.agent.prompt.prompt = kwargs.get('prompt', 'You are a helpful assistant.')
        self.conversation_config.tts = MagicMock()
        self.conversation_config.tts.voice_id = kwargs.get('voice_id', 'voice_test_123')


class MockConversationalAI:
    """Mock for ElevenLabs Conversational AI API."""

    def __init__(self):
        self._agents = []
        self._agent_counter = 0

    def create_agent(self, **kwargs):
        self._agent_counter += 1
        agent_id = f'agent_{"x" * 24}{self._agent_counter:02d}'
        agent = MockElevenLabsAgent(agent_id=agent_id, **kwargs)
        self._agents.append(agent)
        return agent

    def get_agents(self):
        return MagicMock(agents=self._agents)

    def get_agent(self, agent_id):
        return next((a for a in self._agents if a.agent_id == agent_id), None)

    def update_agent(self, agent_id, **kwargs):
        agent = self.get_agent(agent_id)
        if agent:
            for k, v in kwargs.items():
                setattr(agent, k, v)
        return agent

    def delete_agent(self, agent_id):
        self._agents = [a for a in self._agents if a.agent_id != agent_id]


class MockElevenLabsClient:
    """Mock ElevenLabs API client."""

    def __init__(self):
        self.conversational_ai = MockConversationalAI()


@tagged('post_install', '-at_install')
class ElevenLabsTestCase(ConnectTestCase):
    """Base test class for ElevenLabs integration tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.test_voice = cls.env['connect.voice'].create({
            'name': 'Test Voice',
            'provider': 'elevenlabs',
            'external_id': 'voice_' + 'x' * 24,
        })

    @contextmanager
    def mockElevenLabsClient(self):
        """Context manager for mocking ElevenLabs API."""
        mock_client = MockElevenLabsClient()

        with patch.object(
            self.env['connect.settings'].__class__,
            'get_elevenlabs_client',
            return_value=mock_client
        ):
            self._mock_elevenlabs_client = mock_client
            yield mock_client
