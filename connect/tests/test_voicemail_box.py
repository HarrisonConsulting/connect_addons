# -*- coding: utf-8 -*-
"""Tests for the Voicemail Box feature: model, stamping, and record-rule access."""

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestVoicemailBoxModel(ConnectTestCase):
    """Box CRUD, members, and field placement on related models."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Box = cls.env['connect.voicemail_box']
        cls.User = cls.env['connect.user']
        cls.Callflow = cls.env['connect.callflow']
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({'friendly_name': 'VMBox Domain', 'subdomain': 'vmboxd'})
        cls.alice = cls.env['res.users'].create({
            'name': 'Alice Member',
            'login': 'alice_vmbox@example.com',
            'email': 'alice_vmbox@example.com',
        })
        cls.bob = cls.env['res.users'].create({
            'name': 'Bob Member',
            'login': 'bob_vmbox@example.com',
            'email': 'bob_vmbox@example.com',
        })

    def test_box_create_minimal(self):
        box = self.Box.create({'name': 'Sales'})
        self.assertEqual(box.name, 'Sales')
        self.assertTrue(box.active)
        self.assertEqual(box.member_count, 0)
        self.assertEqual(box.voicemail_count, 0)

    def test_box_membership(self):
        box = self.Box.create({
            'name': 'Support',
            'member_ids': [(6, 0, [self.alice.id, self.bob.id])],
        })
        self.assertEqual(box.member_count, 2)
        self.assertIn(self.alice, box.member_ids)
        self.assertIn(self.bob, box.member_ids)

    def test_user_has_voicemail_box_field(self):
        """connect.user.voicemail_box_id m2o exists and is settable."""
        box = self.Box.create({'name': 'Exec'})
        cu = self.User.create({
            'username': 'vmboxtestuser',
            'domain': self.domain.id,
            'voicemail_box_id': box.id,
        })
        self.assertEqual(cu.voicemail_box_id, box)

    def test_callflow_has_voicemail_box_field(self):
        box = self.Box.create({'name': 'IVR Box'})
        cf = self.Callflow.create({
            'name': 'Test Flow',
            'voicemail_enabled': True,
            'voicemail_box_id': box.id,
        })
        self.assertEqual(cf.voicemail_box_id, box)

    def test_call_has_voicemail_box_field(self):
        box = self.Box.create({'name': 'Call Box'})
        call = self._create_test_call(voicemail_box_id=box.id)
        self.assertEqual(call.voicemail_box_id, box)

    def test_voicemail_count_computes(self):
        box = self.Box.create({'name': 'Counted'})
        self._create_test_call(
            voicemail_box_id=box.id,
            voicemail_url='https://api.twilio.com/r/test.mp3',
            direction='incoming',
            status='voicemail',
        )
        box.invalidate_recordset()
        self.assertEqual(box.voicemail_count, 1)


@tagged('post_install', '-at_install')
class TestVoicemailBoxStamping(ConnectTestCase):
    """Box gets stamped on call records at the right moments."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Box = cls.env['connect.voicemail_box']
        cls.Channel = cls.env['connect.channel']
        cls.Call = cls.env['connect.call']
        cls.Callflow = cls.env['connect.callflow']

        cls.member = cls.env['res.users'].create({
            'name': 'Box Member',
            'login': 'member_stamp@example.com',
            'email': 'member_stamp@example.com',
        })
        cls.box = cls.Box.create({
            'name': 'Stamp Box',
            'member_ids': [(6, 0, [cls.member.id])],
        })
        cls.domain = cls.env['connect.domain'].with_context(
            no_twilio_create=True,
        ).create({'friendly_name': 'Stamp Domain', 'subdomain': 'stampd'})
        cls.pbx_user = cls.env['connect.user'].create({
            'username': 'stamppbxuser',
            'domain': cls.domain.id,
            'voicemail_enabled': True,
            'voicemail_box_id': cls.box.id,
            'user': cls.member.id,
        })

    def test_stamp_from_called_pbx_user_on_call_status(self):
        """on_call_status stamps voicemail_box_id when called_pbx_user has a box."""
        params = {
            'CallSid': 'CA_stamp_user_' + 'x' * 20,
            'Caller': '+15551234567',
            'Called': f'sip:{self.pbx_user.username}@example.com',
            'CallStatus': 'ringing',
            'Direction': 'inbound',
            'SequenceNumber': '0',
        }
        self.Call.on_call_status(params)
        channel = self.Channel.search([('sid', '=', params['CallSid'])], limit=1)
        self.assertTrue(channel.call, "Call should have been created")
        self.assertEqual(channel.call.voicemail_box_id, self.box,
                         "Call should be stamped with the called_pbx_user's box")

    def test_stamp_does_not_overwrite_existing_box(self):
        """Once stamped, the box is not overwritten by a later derivation."""
        other = self.Box.create({'name': 'Other'})
        call = self._create_test_call(voicemail_box_id=other.id)
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_existing_box_' + 'x' * 18,
            'caller': '+15551234567',
            'called': f'sip:{self.pbx_user.username}@example.com',
            'called_pbx_user': self.pbx_user.id,
            'status': 'ringing',
            'technical_direction': 'inbound',
        })
        cf = self.Callflow.create({
            'name': 'Stamp Test',
            'voicemail_enabled': True,
            'voicemail_box_id': self.Box.create({'name': 'CF Box'}).id,
        })
        cf._stamp_voicemail_box({'CallSid': channel.sid})
        call.invalidate_recordset()
        self.assertEqual(call.voicemail_box_id, other,
                         "Existing box must not be overwritten")

    def test_callflow_stamps_via_helper(self):
        """_stamp_voicemail_box resolves the call by CallSid and stamps."""
        cf_box = self.Box.create({'name': 'Callflow Box'})
        cf = self.Callflow.create({
            'name': 'CF Stamp',
            'voicemail_enabled': True,
            'voicemail_box_id': cf_box.id,
        })
        call = self._create_test_call()
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_cf_stamp_' + 'x' * 22,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'ringing',
            'technical_direction': 'inbound',
        })
        cf._stamp_voicemail_box({'CallSid': channel.sid})
        call.invalidate_recordset()
        self.assertEqual(call.voicemail_box_id, cf_box)

    def test_callflow_stamp_noop_without_box(self):
        """When callflow has no box, stamp is a no-op."""
        cf = self.Callflow.create({'name': 'NoBox', 'voicemail_enabled': True})
        call = self._create_test_call()
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_no_box_' + 'x' * 25,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'ringing',
            'technical_direction': 'inbound',
        })
        cf._stamp_voicemail_box({'CallSid': channel.sid})
        call.invalidate_recordset()
        self.assertFalse(call.voicemail_box_id)

    def test_callflow_stamp_noop_without_sid(self):
        """Empty request → no crash, no stamp."""
        cf_box = self.Box.create({'name': 'X'})
        cf = self.Callflow.create({
            'name': 'NoSid',
            'voicemail_enabled': True,
            'voicemail_box_id': cf_box.id,
        })
        cf._stamp_voicemail_box({})
        cf._stamp_voicemail_box(None)


@tagged('post_install', '-at_install')
class TestVoicemailBoxWebhook(ConnectTestCase):
    """Voicemail webhook uses box defaults for stage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Box = cls.env['connect.voicemail_box']
        cls.Stage = cls.env['connect.voicemail_stage']
        cls.Channel = cls.env['connect.channel']
        cls.Call = cls.env['connect.call']

    def test_webhook_uses_box_default_stage(self):
        """When the box has a voicemail_stage_id, new VM uses that stage."""
        custom_stage = self.Stage.create({'name': 'Box Custom Stage', 'sequence': 99})
        box = self.Box.create({
            'name': 'StagedBox',
            'voicemail_stage_id': custom_stage.id,
        })
        call = self._create_test_call(
            direction='incoming', status='no-answer',
            voicemail_box_id=box.id,
        )
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_box_stage_' + 'x' * 21,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'no-answer',
            'technical_direction': 'inbound',
        })
        self.Call.on_vm_recording_status({
            'CallSid': channel.sid,
            'RecordingUrl': 'https://api.twilio.com/r/box_stage.mp3',
            'RecordingDuration': '12',
            'RecordingSid': 'REboxstage' + 'x' * 24,
        })
        call.invalidate_recordset()
        self.assertEqual(call.voicemail_stage_id, custom_stage)

    def test_webhook_falls_back_to_global_default_without_box_stage(self):
        """No box stage → falls back to first global stage."""
        first_global = self.Stage.search([], order='sequence asc', limit=1)
        self.assertTrue(first_global, "fixture: at least one global stage must exist")
        box = self.Box.create({'name': 'NoStageBox'})
        call = self._create_test_call(
            direction='incoming', status='no-answer',
            voicemail_box_id=box.id,
        )
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_global_stage_' + 'x' * 18,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'no-answer',
            'technical_direction': 'inbound',
        })
        self.Call.on_vm_recording_status({
            'CallSid': channel.sid,
            'RecordingUrl': 'https://api.twilio.com/r/g.mp3',
            'RecordingDuration': '5',
            'RecordingSid': 'REg' + 'x' * 30,
        })
        call.invalidate_recordset()
        self.assertEqual(call.voicemail_stage_id, first_global)


@tagged('post_install', '-at_install')
class TestVoicemailBoxAccess(ConnectTestCase):
    """Record rules: members see their boxes and the calls within."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Box = cls.env['connect.voicemail_box']
        cls.Call = cls.env['connect.call']
        connect_user_group = cls.env.ref('connect.group_connect_user')

        cls.alice = cls.env['res.users'].create({
            'name': 'Alice',
            'login': 'alice_access@example.com',
            'email': 'alice_access@example.com',
            'group_ids': [(4, connect_user_group.id)],
        })
        cls.bob = cls.env['res.users'].create({
            'name': 'Bob',
            'login': 'bob_access@example.com',
            'email': 'bob_access@example.com',
            'group_ids': [(4, connect_user_group.id)],
        })
        cls.sales_box = cls.Box.create({
            'name': 'Sales Access Box',
            'member_ids': [(6, 0, [cls.alice.id])],
        })

    def test_member_can_read_their_box(self):
        box_as_alice = self.sales_box.with_user(self.alice)
        self.assertEqual(box_as_alice.name, 'Sales Access Box')

    def test_non_member_cannot_read_box(self):
        with self.assertRaises(AccessError):
            self.sales_box.with_user(self.bob).read(['name'])

    def test_member_can_read_call_in_their_box(self):
        """Alice is a box member; she can read any call stamped with her box."""
        call = self._create_test_call(
            direction='incoming', status='voicemail',
            voicemail_url='https://api.twilio.com/r/vm.mp3',
            voicemail_box_id=self.sales_box.id,
        )
        call_as_alice = call.with_user(self.alice)
        self.assertEqual(call_as_alice.voicemail_url, 'https://api.twilio.com/r/vm.mp3',
                         "Box member must be able to read box-stamped calls")

    def test_non_member_cannot_read_call_in_box(self):
        """Bob is not in Sales box; he can't see Sales-box-stamped calls
        unless he's otherwise involved (caller/called/answered)."""
        call = self._create_test_call(
            direction='incoming', status='voicemail',
            voicemail_url='https://api.twilio.com/r/vm.mp3',
            voicemail_box_id=self.sales_box.id,
        )
        with self.assertRaises(AccessError):
            call.with_user(self.bob).read(['voicemail_url'])

    def test_box_member_sees_non_voicemail_call(self):
        """The record rule applies to ALL calls in the box, not just voicemails."""
        call = self._create_test_call(
            direction='incoming', status='completed',
            voicemail_box_id=self.sales_box.id,
        )
        call_as_alice = call.with_user(self.alice)
        self.assertEqual(call_as_alice.status, 'completed',
                         "Box members see full call history, not only voicemails")

    def test_member_who_is_also_assignee_still_sees(self):
        """Members already directly involved aren't blocked by box stamping."""
        call = self._create_test_call(
            direction='incoming', status='voicemail',
            voicemail_url='https://api.twilio.com/r/vm.mp3',
            voicemail_box_id=self.sales_box.id,
            called_users=[(6, 0, [self.alice.id])],
        )
        self.assertEqual(call.with_user(self.alice).voicemail_url,
                         'https://api.twilio.com/r/vm.mp3')

    def test_user_cannot_create_box(self):
        """Regular connect users cannot create boxes (admin-only)."""
        with self.assertRaises(AccessError):
            self.Box.with_user(self.alice).create({'name': 'Sneaky'})

    def test_my_voicemails_filter_includes_box_calls(self):
        """The widened 'my voicemails' domain returns box calls for the member."""
        call = self._create_test_call(
            direction='incoming', status='voicemail',
            voicemail_url='https://api.twilio.com/r/vm.mp3',
            voicemail_box_id=self.sales_box.id,
        )
        domain = ['|',
                  ('voicemail_assignee_ids', 'in', [self.alice.id]),
                  ('voicemail_box_id.member_ids', 'in', [self.alice.id])]
        found = self.Call.with_user(self.alice).search(domain)
        self.assertIn(call, found)
