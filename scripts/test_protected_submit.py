"""Protect foreign drafts and retain the distinction between paste and submit."""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import cmux_bridge as bridge
import cmux_direct_input as direct


class ProtectedSubmitTests(unittest.TestCase):
    def setUp(self):
        self.marker = 'DRAFT_INCIDENT_REGRESSION'
        self.text = 'STATUS: ' + self.marker + ' 任务请继续'
        self.proof = {'workspace_uuid': 'WORKSPACE', 'target_surface_uuid': 'TARGET'}
        self.events, self.keys = [], []
        self.ack = {'submitted': True}
        self.input_state = dict(surface_id='TARGET', workspace_id='WORKSPACE',
            agent=True, terminal=True, state='empty', blocks_typing=False,
            lifecycle='running')
        self.rc = 0
        self.pin = patch.object(bridge, 'pin_workspace', return_value=self.proof)
        self.pin.start(); self.addCleanup(self.pin.stop)
        self.run = patch.object(bridge.subprocess, 'run', side_effect=self.process).start()
        self.addCleanup(patch.stopall)

    def process(self, argv, **kwargs):
        if 'surface.input_state' in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.input_state), '')
        return subprocess.CompletedProcess(argv, self.rc, json.dumps(self.ack), 'refused draft')

    def send(self, screen='Compacting context'):
        return direct.send_once(self.text, self.marker, recheck=lambda: None,
            preflight=lambda: bridge.require_prompt_free('target'),
            emit=lambda phase, *args: self.events.append({'phase': phase}),
            submit=lambda text: bridge.submit_text_protected('target', text),
            paste=Mock(side_effect=AssertionError('no raw fallback')),
            key=self.keys.append,
            observe=lambda: {'state': 'NATIVE_PENDING', 'confirmed': False},
            read_screen=lambda: screen, wait_seconds=0)

    def test_nonzero_refusal_never_sends_a_separate_enter(self):
        self.rc = 1
        with self.assertRaisesRegex(RuntimeError, 'rc=1'):
            self.send()
        self.assertEqual(self.run.call_count, 2)  # Read-only state, then one paste request.
        self.assertEqual(self.keys, [])
        self.assertNotIn('ENTER_SENT', [e['phase'] for e in self.events])

    def test_unknown_submission_never_falls_back_or_gets_recovery_permission(self):
        self.ack = {}
        with self.assertRaisesRegex(RuntimeError, 'OUTCOME_UNKNOWN'):
            self.send()
        self.assertEqual(self.keys, [])
        self.assertNotIn('PASTED', [e['phase'] for e in self.events])

    def test_query_failure_or_unknown_fields_precede_all_input_intents(self):
        for state in ({}, dict(self.input_state, blocks_typing=None),
                      dict(self.input_state, surface_id='OTHER')):
            self.input_state = state
            with self.assertRaisesRegex(RuntimeError, 'PROMPT_STATE_UNKNOWN'):
                self.send()
            self.assertEqual(self.events, [])
            self.assertEqual(self.keys, [])
        self.run.side_effect = RuntimeError('input_state unavailable')
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            self.send()
        self.assertEqual(self.events, [])

    def test_foreign_draft_and_dialog_are_refused_before_paste(self):
        for state in ('draft', 'dialog'):
            self.input_state.update(state=state, blocks_typing=True)
            with self.assertRaisesRegex(RuntimeError, 'PROMPT_NOT_EMPTY'):
                self.send()
            self.assertEqual(self.events, [])
            self.assertEqual(self.keys, [])

    def test_host_queued_paste_never_authorizes_additional_key(self):
        self.ack = {'delivery': 'queued'}
        result = self.send('❯ ' + self.text)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['extra_enter_deferred'], 'HOST_INPUT_QUEUED')
        self.assertIn('PASTE_QUEUED', [e['phase'] for e in self.events])
        self.assertNotIn('PASTED', [e['phase'] for e in self.events])
        self.assertEqual(self.keys, [])
        original = {'native_binding': {'receipt_contract': 'native-user-marker-v1',
                    'receipt_marker': self.marker}, 'events': self.events}
        with self.assertRaisesRegex(ValueError, 'BUDGET_UNAVAILABLE'):
            direct.recover_once(self.text, self.marker, original=original,
                recheck=lambda: None, emit=Mock(), key=self.keys.append,
                observe=Mock(), read_screen=Mock(), wait_seconds=0)

    def test_zero_exit_with_declined_submit_does_not_claim_enter_sent(self):
        self.ack = {'submitted': False, 'submit_error': 'compacting'}
        result = self.send()
        self.assertFalse(result['confirmed'])
        phases = [e['phase'] for e in self.events]
        self.assertIn('PASTED', phases)
        self.assertIn('ENTER_NOT_SENT', phases)
        self.assertNotIn('ENTER_SENT', phases)
        self.assertEqual(self.keys, [])
        original = {'native_binding': {'receipt_contract': 'native-user-marker-v1',
                    'receipt_marker': self.marker}, 'events': self.events}
        def observe():
            return {'confirmed': bool(self.keys),
                    'state': 'NATIVE_RECEIVED' if self.keys else 'NATIVE_PENDING'}
        result = direct.recover_once(self.text, self.marker, original=original,
            recheck=lambda: None, emit=lambda phase, *args: self.events.append({'phase': phase}),
            key=self.keys.append, observe=observe, read_screen=lambda: '❯ ' + self.text,
            wait_seconds=0)
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.keys, ['enter'])
        with self.assertRaisesRegex(ValueError, 'BUDGET_UNAVAILABLE'):
            direct.recover_once(self.text, self.marker, original=original,
                recheck=lambda: None, emit=Mock(), key=self.keys.append,
                observe=observe, read_screen=Mock(), wait_seconds=0)

    def test_protected_command_preserves_literal_text_and_pins_target(self):
        text = '--force literal \\n 中文 "quote"\nsecond line'
        result = bridge.submit_text_protected('target', text)
        self.assertTrue(result['submitted'])
        argv = self.run.call_args.args[0]
        self.assertEqual(argv[1:3], ['--json', 'paste'])
        self.assertEqual(argv[argv.index('--workspace') + 1], 'WORKSPACE')
        self.assertEqual(argv[argv.index('--surface') + 1], 'TARGET')
        self.assertIn('--submit', argv)
        self.assertNotIn('--force', argv[:-1])
        self.assertEqual(argv[-2:], ['--', text])

    def test_sanitized_controls_are_refused_before_transport(self):
        for char in ('\x00', '\x03', '\x1b', '\x7f'):
            with self.assertRaisesRegex(ValueError, 'CONTROL_CHARACTER'):
                bridge.submit_text_protected('target', self.text + char)
        self.run.assert_not_called()

    def test_successor_state_query_pins_both_endpoints_without_granting_input(self):
        target = '00000000-0000-4000-8000-000000000003'
        workspace = '00000000-0000-4000-8000-000000000001'
        proof = dict(target_surface_uuid=target, target_workspace_uuid=workspace)
        params = dict(surface_id=target, workspace_id=workspace)
        with patch.object(bridge, 'pin_successor_pair', return_value=proof):
            bridge._run_successor('rpc', 'surface.input_state', json.dumps(params),
                artifact={}, receipt_contract='native-user-marker-v1')
            self.assertIn('surface.input_state', self.run.call_args.args[0])
            self.run.reset_mock()
            params['surface_id'] = '00000000-0000-4000-8000-000000000004'
            with self.assertRaisesRegex(bridge.WorkspaceScopeError, 'state target override'):
                bridge._run_successor('rpc', 'surface.input_state', json.dumps(params),
                    artifact={}, receipt_contract='native-user-marker-v1')
            self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
