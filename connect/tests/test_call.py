# -*- coding: utf-8 -*-
"""Tests for connect.call model."""

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestCall(ConnectTestCase):
    """Test connect.call model functionality."""

    def test_call_create_basic(self):
        """Test basic call creation."""
        call = self._create_test_call(
            direction='incoming',
            status='completed',
            caller='+15551234567',
            called='+15559876543',
        )
        self.assertTrue(call.id)
        self.assertEqual(call.direction, 'incoming')
        self.assertEqual(call.status, 'completed')
        self.assertEqual(call.caller, '+15551234567')
        self.assertEqual(call.called, '+15559876543')

    def test_call_duration_human_format(self):
        """Test duration_human computed field."""
        call = self._create_test_call()
        call.duration = 125  # 2 minutes 5 seconds
        self.assertEqual(call.duration_human, '02:05')

    def test_call_duration_zero(self):
        """Test duration_human when duration is 0."""
        call = self._create_test_call()
        call.duration = 0
        self.assertEqual(call.duration_human, '00:00')

    def test_call_duration_minutes(self):
        """Test duration_minutes computed field."""
        call = self._create_test_call()
        call.duration = 125
        self.assertAlmostEqual(call.duration_minutes, 2.08, places=2)

    def test_call_with_partner(self):
        """Test call creation with partner."""
        call = self._create_test_call(partner=self.partner_1.id)
        self.assertEqual(call.partner.id, self.partner_1.id)

    def test_call_directions(self):
        """Test incoming and outgoing call directions."""
        incoming = self._create_test_call(direction='incoming')
        outgoing = self._create_test_call(direction='outgoing')

        self.assertEqual(incoming.direction, 'incoming')
        self.assertEqual(outgoing.direction, 'outgoing')


@tagged('post_install', '-at_install')
class TestCallChannel(ConnectTestCase):
    """Test call with channel relationship."""

    def test_call_with_channel(self):
        """Test call creation with associated channel."""
        call = self._create_test_call(create_channel=True)
        self.assertTrue(call.channels)
        self.assertEqual(len(call.channels), 1)
        self.assertEqual(call.channels[0].call.id, call.id)


@tagged('post_install', '-at_install')
class TestCallFinalization(ConnectTestCase):
    """Test call finalization state machine (_finalize_call_details)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create TwiML app for domain requirement
        cls.twiml_app = cls.env['connect.twiml'].create({
            'name': 'Test App',
        })
        # Create domain for connect.user requirement
        cls.connect_domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Test Domain',
            'subdomain': 'test-domain',
            'application': cls.twiml_app.id,
        })
        # Create Odoo users
        cls.user_alice = cls.env['res.users'].create({
            'name': 'Alice Test',
            'login': 'alice_test_call',
            'email': 'alice@test.com',
        })
        cls.user_bob = cls.env['res.users'].create({
            'name': 'Bob Test',
            'login': 'bob_test_call',
            'email': 'bob@test.com',
        })
        cls.user_carol = cls.env['res.users'].create({
            'name': 'Carol Test',
            'login': 'carol_test_call',
            'email': 'carol@test.com',
        })
        # Create PBX users linked to Odoo users
        cls.pbx_alice = cls.env['connect.user'].create({
            'username': 'alice',
            'domain': cls.connect_domain.id,
            'user': cls.user_alice.id,
        })
        cls.pbx_bob = cls.env['connect.user'].create({
            'username': 'bob',
            'domain': cls.connect_domain.id,
            'user': cls.user_bob.id,
        })
        cls.pbx_carol = cls.env['connect.user'].create({
            'username': 'carol',
            'domain': cls.connect_domain.id,
            'user': cls.user_carol.id,
        })

    def _create_call_with_channels(self, direction='incoming', channel_configs=None):
        """Create a call with channels in specified configurations.

        Args:
            direction: 'incoming' or 'outgoing'
            channel_configs: list of dicts, each with keys:
                - status: channel status (default 'no-answer')
                - called_pbx_user: connect.user record (optional)
                - caller_pbx_user: connect.user record (optional)
                - call_source: selection value (optional)
                - parent: bool, if True this is the root/parent channel (default False)
                - duration: int seconds (optional)
                - technical_direction: str (optional)
        Returns:
            (call, channels) tuple
        """
        call = self.env['connect.call'].create({
            'direction': direction,
            'status': 'ringing',
            'caller': '+15551234567',
            'called': '+15559876543',
        })
        channels = self.env['connect.channel']
        parent_channel = None

        for i, config in enumerate(channel_configs or []):
            is_parent = config.get('parent', False)
            vals = {
                'call': call.id,
                'sid': f'CH{call.id:04d}{i:02d}',
                'caller': '+15551234567',
                'called': '+15559876543',
                'status': config.get('status', 'no-answer'),
                'technical_direction': config.get(
                    'technical_direction',
                    'inbound' if direction == 'incoming' else 'outbound-api'
                ),
            }
            if config.get('called_pbx_user'):
                vals['called_pbx_user'] = config['called_pbx_user'].id
                if config['called_pbx_user'].user:
                    vals['called_user'] = config['called_pbx_user'].user.id
            if config.get('caller_pbx_user'):
                vals['caller_pbx_user'] = config['caller_pbx_user'].id
                if config['caller_pbx_user'].user:
                    vals['caller_user'] = config['caller_pbx_user'].user.id
            if config.get('call_source'):
                vals['call_source'] = config['call_source']
            if config.get('duration') is not None:
                vals['duration'] = config['duration']
            if not is_parent and parent_channel:
                vals['parent_channel'] = parent_channel.id
                vals['parent_sid'] = parent_channel.sid

            channel = self.env['connect.channel'].create(vals)
            if is_parent:
                parent_channel = channel
            channels |= channel

        return call, channels

    # ------------------------------------------------------------------
    # Finalization state machine tests
    # ------------------------------------------------------------------

    def test_finalize_sets_is_finalized(self):
        """Finalization marks call as finalized."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 30},
                {'status': 'completed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call', 'duration': 30},
            ]
        )
        self.assertFalse(call.is_finalized)
        call._finalize_call_details()
        self.assertTrue(call.is_finalized)

    def test_finalize_idempotent(self):
        """Calling finalize twice doesn't error or change data."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 30},
                {'status': 'completed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call', 'duration': 30},
            ]
        )
        call._finalize_call_details()
        self.assertTrue(call.is_finalized)
        first_answered_user = call.answered_user
        first_status = call.status
        first_pattern = call.call_pattern

        # Second call should be a no-op
        call._finalize_call_details()
        self.assertTrue(call.is_finalized)
        self.assertEqual(call.answered_user, first_answered_user)
        self.assertEqual(call.status, first_status)
        self.assertEqual(call.call_pattern, first_pattern)

    def test_finalize_populates_answered_user(self):
        """Finalization sets answered_user from the completed channel."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 45},
                {'status': 'completed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call', 'duration': 45},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.answered_user, self.user_alice)
        self.assertEqual(call.answered_pbx_user, self.pbx_alice)

    def test_finalize_populates_called_users(self):
        """Finalization sets called_users from all dialed channels.

        called_users is populated incrementally during channel creation
        (via _update_channel), so we set it before finalization to simulate
        the real webhook flow.
        """
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 30},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'ring_group'},
                {'status': 'completed', 'called_pbx_user': self.pbx_bob,
                 'call_source': 'ring_group', 'duration': 30},
            ]
        )
        # Simulate the called_users population that happens during webhook processing
        call.called_users = [(4, self.user_alice.id), (4, self.user_bob.id)]

        call._finalize_call_details()
        self.assertIn(self.user_alice, call.called_users)
        self.assertIn(self.user_bob, call.called_users)

    def test_finalize_with_no_channels(self):
        """Finalization handles call with zero channels gracefully."""
        call = self.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'ringing',
            'caller': '+15551234567',
            'called': '+15559876543',
        })
        # Should not raise
        call._finalize_call_details()
        self.assertTrue(call.is_finalized)

    def test_finalize_sets_call_result_answered(self):
        """Completed call with answered channel -> call_result='answered'."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 60},
                {'status': 'completed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call', 'duration': 60},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.answered_user, self.user_alice)
        self.assertEqual(call.status, 'completed')
        self.assertEqual(call.call_result, 'answered')

    def test_finalize_sets_call_result_missed(self):
        """Call with no answered channels -> call_result='missed'."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'no-answer'},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call'},
            ]
        )
        call._finalize_call_details()
        self.assertFalse(call.answered_user)
        self.assertEqual(call.status, 'no-answer')
        self.assertEqual(call.call_result, 'missed')

    def test_finalize_sets_call_result_voicemail(self):
        """Call with voicemail_url -> call_result='voicemail'."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 20},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call'},
            ]
        )
        call.voicemail_url = 'https://api.twilio.com/recordings/REtest123'
        call._finalize_call_details()
        self.assertEqual(call.call_result, 'voicemail')

    def test_finalize_clears_transfer_context(self):
        """Finalization clears the transfer_context field."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 30},
                {'status': 'completed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call', 'duration': 30},
            ]
        )
        call.transfer_context = {'target': 'bob'}
        call._finalize_call_details()
        self.assertFalse(call.transfer_context)

    # ------------------------------------------------------------------
    # _set_final_call_status tests
    # ------------------------------------------------------------------

    def test_final_status_outgoing_always_completed(self):
        """Outgoing calls always get status 'completed'."""
        call, _ = self._create_call_with_channels(
            direction='outgoing',
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 10,
                 'caller_pbx_user': self.pbx_alice,
                 'technical_direction': 'outbound-api'},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.status, 'completed')

    def test_final_status_failed_channel(self):
        """Incoming call with failed channel gets 'failed' status."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'failed'},
                {'status': 'failed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call'},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.status, 'failed')

    def test_final_status_busy_channel(self):
        """Incoming call with busy channel gets 'busy' status."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'busy'},
                {'status': 'busy', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call'},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.status, 'busy')


@tagged('post_install', '-at_install')
class TestCallPatternDetection(ConnectTestCase):
    """Test call pattern detection (_detect_call_pattern)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({'name': 'Test App'})
        cls.connect_domain = cls.env['connect.domain'].create({
            'friendly_name': 'Test Pattern Domain',
            'subdomain': 'test-pattern',
            'application': cls.twiml_app.id,
        })
        cls.user_alice = cls.env['res.users'].create({
            'name': 'Alice Pattern',
            'login': 'alice_pattern',
            'email': 'alice_p@test.com',
        })
        cls.user_bob = cls.env['res.users'].create({
            'name': 'Bob Pattern',
            'login': 'bob_pattern',
            'email': 'bob_p@test.com',
        })
        cls.pbx_alice = cls.env['connect.user'].create({
            'username': 'alicep',
            'domain': cls.connect_domain.id,
            'user': cls.user_alice.id,
        })
        cls.pbx_bob = cls.env['connect.user'].create({
            'username': 'bobp',
            'domain': cls.connect_domain.id,
            'user': cls.user_bob.id,
        })

    def _make_call_with_child_channels(self, channel_configs):
        """Helper to create a call with a parent channel and child channels."""
        call = self.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'ringing',
            'caller': '+15551234567',
            'called': '+15559876543',
        })
        parent = self.env['connect.channel'].create({
            'call': call.id,
            'sid': f'CHparent{call.id:04d}',
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'ringing',
            'technical_direction': 'inbound',
        })
        for i, config in enumerate(channel_configs):
            self.env['connect.channel'].create({
                'call': call.id,
                'sid': f'CHchild{call.id:04d}{i:02d}',
                'caller': '+15551234567',
                'called': '+15559876543',
                'status': config.get('status', 'no-answer'),
                'technical_direction': 'outbound-api',
                'parent_channel': parent.id,
                'parent_sid': parent.sid,
                'called_pbx_user': config['called_pbx_user'].id,
                'called_user': config['called_pbx_user'].user.id,
                'call_source': config.get('call_source'),
            })
        return call

    def test_detect_pattern_direct_call(self):
        """Single called_pbx_user channel with direct_call source -> 'direct_call'."""
        call = self._make_call_with_child_channels([
            {'called_pbx_user': self.pbx_alice, 'call_source': 'direct_call'},
        ])
        pattern = call._detect_call_pattern()
        self.assertEqual(pattern, 'direct_call')

    def test_detect_pattern_ring_group(self):
        """Multiple called channels with ring_group source -> 'ring_group'."""
        call = self._make_call_with_child_channels([
            {'called_pbx_user': self.pbx_alice, 'call_source': 'ring_group'},
            {'called_pbx_user': self.pbx_bob, 'call_source': 'ring_group'},
        ])
        pattern = call._detect_call_pattern()
        self.assertEqual(pattern, 'ring_group')

    def test_detect_pattern_from_channel_source(self):
        """Pattern detected from channel.call_source when explicitly set."""
        call = self._make_call_with_child_channels([
            {'called_pbx_user': self.pbx_alice, 'call_source': 'ring_group'},
        ])
        # Even a single channel tagged as ring_group should detect ring_group
        pattern = call._detect_call_pattern()
        self.assertEqual(pattern, 'ring_group')

    def test_detect_pattern_fallback(self):
        """Fallback detection when no explicit call_source on channels."""
        call = self._make_call_with_child_channels([
            {'called_pbx_user': self.pbx_alice, 'call_source': None},
        ])
        # No call_source set, falls back to counting unique users
        pattern = call._detect_call_pattern()
        # Single user -> direct_call via fallback
        self.assertEqual(pattern, 'direct_call')

    def test_detect_pattern_fallback_multiple_users(self):
        """Fallback detection with multiple users yields ring_group."""
        call = self._make_call_with_child_channels([
            {'called_pbx_user': self.pbx_alice, 'call_source': None},
            {'called_pbx_user': self.pbx_bob, 'call_source': None},
        ])
        pattern = call._detect_call_pattern()
        self.assertEqual(pattern, 'ring_group')

    def test_detect_pattern_cached(self):
        """Already-set call_pattern is returned without re-detection."""
        call = self._make_call_with_child_channels([
            {'called_pbx_user': self.pbx_alice, 'call_source': 'ring_group'},
        ])
        call.call_pattern = 'direct_call'  # Override
        pattern = call._detect_call_pattern()
        self.assertEqual(pattern, 'direct_call')

    def test_detect_pattern_no_channels(self):
        """Call with no channels returns None."""
        call = self.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'ringing',
            'caller': '+15551234567',
            'called': '+15559876543',
        })
        self.assertIsNone(call._detect_call_pattern())

    def test_detect_pattern_no_child_channels(self):
        """Call with only a parent channel (no children) returns None."""
        call = self.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'ringing',
            'caller': '+15551234567',
            'called': '+15559876543',
        })
        self.env['connect.channel'].create({
            'call': call.id,
            'sid': 'CHparent_only',
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'ringing',
            'technical_direction': 'inbound',
        })
        self.assertIsNone(call._detect_call_pattern())


@tagged('post_install', '-at_install')
class TestCallUserPopulation(ConnectTestCase):
    """Test user field population during finalization."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twiml_app = cls.env['connect.twiml'].create({'name': 'Test App'})
        cls.connect_domain = cls.env['connect.domain'].create({
            'friendly_name': 'Test UserPop Domain',
            'subdomain': 'test-userpop',
            'application': cls.twiml_app.id,
        })
        cls.user_alice = cls.env['res.users'].create({
            'name': 'Alice Pop',
            'login': 'alice_pop',
            'email': 'alice_pop@test.com',
        })
        cls.user_bob = cls.env['res.users'].create({
            'name': 'Bob Pop',
            'login': 'bob_pop',
            'email': 'bob_pop@test.com',
        })
        cls.user_carol = cls.env['res.users'].create({
            'name': 'Carol Pop',
            'login': 'carol_pop',
            'email': 'carol_pop@test.com',
        })
        cls.pbx_alice = cls.env['connect.user'].create({
            'username': 'alicepop',
            'domain': cls.connect_domain.id,
            'user': cls.user_alice.id,
        })
        cls.pbx_bob = cls.env['connect.user'].create({
            'username': 'bobpop',
            'domain': cls.connect_domain.id,
            'user': cls.user_bob.id,
        })
        cls.pbx_carol = cls.env['connect.user'].create({
            'username': 'carolpop',
            'domain': cls.connect_domain.id,
            'user': cls.user_carol.id,
        })

    def _create_call_with_channels(self, direction='incoming', channel_configs=None):
        """Create a call with channels in specified configurations."""
        call = self.env['connect.call'].create({
            'direction': direction,
            'status': 'ringing',
            'caller': '+15551234567',
            'called': '+15559876543',
        })
        channels = self.env['connect.channel']
        parent_channel = None

        for i, config in enumerate(channel_configs or []):
            is_parent = config.get('parent', False)
            vals = {
                'call': call.id,
                'sid': f'CH{call.id:04d}{i:02d}',
                'caller': '+15551234567',
                'called': '+15559876543',
                'status': config.get('status', 'no-answer'),
                'technical_direction': config.get(
                    'technical_direction',
                    'inbound' if direction == 'incoming' else 'outbound-api'
                ),
            }
            if config.get('called_pbx_user'):
                vals['called_pbx_user'] = config['called_pbx_user'].id
                if config['called_pbx_user'].user:
                    vals['called_user'] = config['called_pbx_user'].user.id
            if config.get('caller_pbx_user'):
                vals['caller_pbx_user'] = config['caller_pbx_user'].id
                if config['caller_pbx_user'].user:
                    vals['caller_user'] = config['caller_pbx_user'].user.id
            if config.get('call_source'):
                vals['call_source'] = config['call_source']
            if config.get('duration') is not None:
                vals['duration'] = config['duration']
            if not is_parent and parent_channel:
                vals['parent_channel'] = parent_channel.id
                vals['parent_sid'] = parent_channel.sid

            channel = self.env['connect.channel'].create(vals)
            if is_parent:
                parent_channel = channel
            channels |= channel

        return call, channels

    def test_populate_direct_call_users(self):
        """Direct call populates caller and answered user correctly."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 45},
                {'status': 'completed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call', 'duration': 45},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.call_pattern, 'direct_call')
        self.assertEqual(call.answered_user, self.user_alice)
        self.assertEqual(call.answered_pbx_user, self.pbx_alice)
        self.assertEqual(call.completed_by_user, self.user_alice)

    def test_populate_direct_call_no_answer(self):
        """Direct call where nobody answers leaves answered_user empty."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'no-answer'},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call'},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.call_pattern, 'direct_call')
        self.assertFalse(call.answered_user)
        self.assertFalse(call.answered_pbx_user)

    def test_populate_ring_group_users(self):
        """Ring group populates all called users and single answered user."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 30},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'ring_group'},
                {'status': 'completed', 'called_pbx_user': self.pbx_bob,
                 'call_source': 'ring_group', 'duration': 30},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_carol,
                 'call_source': 'ring_group'},
            ]
        )
        # Simulate webhook-driven called_users population
        call.called_users = [
            (4, self.user_alice.id),
            (4, self.user_bob.id),
            (4, self.user_carol.id),
        ]
        call._finalize_call_details()
        self.assertEqual(call.call_pattern, 'ring_group')
        # Bob answered (completed channel)
        self.assertEqual(call.answered_user, self.user_bob)
        self.assertEqual(call.completed_by_user, self.user_bob)
        # All three users were called
        self.assertEqual(len(call.called_users), 3)

    def test_populate_ring_group_nobody_answers(self):
        """Ring group where nobody answers leaves answered_user empty."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'no-answer'},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'ring_group'},
                {'status': 'no-answer', 'called_pbx_user': self.pbx_bob,
                 'call_source': 'ring_group'},
            ]
        )
        call._finalize_call_details()
        self.assertEqual(call.call_pattern, 'ring_group')
        self.assertFalse(call.answered_user)

    def test_populate_outgoing_call_users(self):
        """Outgoing call sets caller_user to the originating user."""
        call, _ = self._create_call_with_channels(
            direction='outgoing',
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 60,
                 'caller_pbx_user': self.pbx_alice,
                 'technical_direction': 'outbound-api'},
                {'status': 'completed', 'duration': 55,
                 'technical_direction': 'outbound-dial'},
            ]
        )
        # Pattern set explicitly since outgoing calls to external numbers
        # have no called_pbx_user for automatic detection
        call.call_pattern = 'direct_call'
        call._finalize_call_details()
        self.assertEqual(call.status, 'completed')
        # Original caller should be set as completed_by_user
        self.assertEqual(call.completed_by_user, self.user_alice)

    def test_populate_users_no_pbx_user(self):
        """External caller (no PBX user) still works through finalization."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'no-answer'},
            ]
        )
        # No child channels with pbx users - simulates external-only call
        call._finalize_call_details()
        self.assertTrue(call.is_finalized)
        self.assertFalse(call.answered_user)
        self.assertFalse(call.answered_pbx_user)

    def test_populate_fallback_when_no_pattern(self):
        """Fallback population when pattern detection returns None."""
        call = self.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'ringing',
            'caller': '+15551234567',
            'called': '+15559876543',
        })
        # Create channels without parent/child relationship (no pattern detectable)
        self.env['connect.channel'].create({
            'call': call.id,
            'sid': 'CH_fb_01',
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'completed',
            'technical_direction': 'inbound',
            'called_pbx_user': self.pbx_alice.id,
            'called_user': self.user_alice.id,
            'duration': 30,
        })
        call._finalize_call_details()
        self.assertTrue(call.is_finalized)
        # Fallback should pick up the completed channel
        self.assertEqual(call.answered_user, self.user_alice)

    def test_populate_direct_call_with_transfer(self):
        """Direct call with transfer sets answered_user to initial answerer."""
        call, _ = self._create_call_with_channels(
            channel_configs=[
                {'parent': True, 'status': 'completed', 'duration': 60},
                {'status': 'completed', 'called_pbx_user': self.pbx_alice,
                 'call_source': 'direct_call', 'duration': 50},
                {'status': 'completed', 'called_pbx_user': self.pbx_bob,
                 'call_source': 'transfer', 'duration': 30},
            ]
        )
        # Mark bob as transfer recipient
        call.transferred_users = [(4, self.user_bob.id)]
        call._finalize_call_details()
        # Alice was the initial answerer
        self.assertEqual(call.answered_user, self.user_alice)
        # Bob completed the call as transfer recipient
        self.assertEqual(call.completed_by_user, self.user_bob)


@tagged('post_install', '-at_install')
class TestCallAnalyticsFields(ConnectTestCase):
    """Test computed analytics fields (call_result, is_missed, etc.)."""

    def test_call_result_voicemail_takes_priority(self):
        """Voicemail URL takes priority over other status indicators."""
        call = self._create_test_call(direction='incoming', status='completed')
        call.voicemail_url = 'https://api.twilio.com/recordings/RE123'
        self.assertEqual(call.call_result, 'voicemail')
        self.assertFalse(call.is_missed)

    def test_call_result_busy(self):
        """Busy status maps to 'busy' call_result."""
        call = self._create_test_call(direction='incoming', status='busy')
        self.assertEqual(call.call_result, 'busy')
        self.assertFalse(call.is_missed)

    def test_call_result_failed(self):
        """Failed status maps to 'failed' call_result."""
        call = self._create_test_call(direction='incoming', status='failed')
        self.assertEqual(call.call_result, 'failed')
        self.assertFalse(call.is_missed)

    def test_call_result_canceled(self):
        """Canceled status maps to 'failed' call_result."""
        call = self._create_test_call(direction='incoming', status='canceled')
        self.assertEqual(call.call_result, 'failed')

    def test_call_result_missed_incoming(self):
        """Incoming no-answer without answered_user is 'missed'."""
        call = self._create_test_call(direction='incoming', status='no-answer')
        self.assertEqual(call.call_result, 'missed')
        self.assertTrue(call.is_missed)

    def test_call_result_answered_with_user(self):
        """Completed call with answered_user is 'answered'."""
        call = self._create_test_call(direction='incoming', status='completed')
        user = self.env['res.users'].create({
            'name': 'Answerer',
            'login': 'answerer_analytics',
            'email': 'answerer@test.com',
        })
        call.answered_user = user
        self.assertEqual(call.call_result, 'answered')
        self.assertFalse(call.is_missed)

    def test_call_result_answered_completed_no_user(self):
        """Completed incoming call without answered_user is classified as 'missed'."""
        call = self._create_test_call(direction='incoming', status='completed')
        self.assertEqual(call.call_result, 'missed')
