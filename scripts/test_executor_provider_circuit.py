"""Distinct provider outcomes, genuine rearm and file fallback; no live input."""
import copy
import os
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from unittest.mock import Mock, patch

import executor_provider_circuit as circuit
import executor_reask as reask
import cmux_native_delivery as native
import cmux_codex_input as direct
import cmux_bridge as bridge
from native_test_support import NativeFixture
import executor_reask_lifecycle as lifecycle

AZURE = dict(message='The requested item was created under a different Azure OpenAI resource. Use the same resource that created the item to access it.')


def binding(path='/original/session.jsonl'):
    return dict(provider='codex', session_id=str(uuid.uuid4()),
        identity=dict(target_surface_uuid=str(uuid.uuid4()), workspace_uuid=str(uuid.uuid4())),
        process=dict(pid=123, birth=[1, 2], executable='/original/codex'),
        transcript=dict(path=path, device=1, inode=2))


def outcome(kind='API_ERROR', error=None, **extra):
    row = dict(attempt_id=str(uuid.uuid4()), kind=kind, terminal=True,
        error=AZURE if error is None and kind == 'API_ERROR' else error)
    row.update(extra)
    return row


def observation(bound, rows):
    return dict(source='codex-native-terminal-outcomes-v1', thread_id=bound['session_id'],
        transcript_path=bound['transcript']['path'], observed_at_epoch=10, outcomes=rows)


class CircuitTests(unittest.TestCase):
    def setUp(self):
        self.bound, self.state = binding(), {}

    def apply(self, rows):
        return circuit.apply_observation(self.state, self.bound, observation(self.bound, rows))

    def test_three_distinct_failures_open_once_and_duplicate_polls_do_not_rearm(self):
        rows = [outcome() for _ in range(3)]
        self.assertFalse(self.apply(rows[:2])['suppress'])
        result = self.apply(rows)
        self.assertTrue(result['suppress'])
        self.assertEqual(result['distinct_failures'], 3)
        self.assertIsNotNone(result['operator_status'])
        for _ in range(4):
            result = self.apply(rows)
            self.assertTrue(result['suppress'])
            self.assertIsNone(result['operator_status'])
        self.assertEqual(self.state['provider_circuit']['open_count'], 1)

    def test_only_real_terminal_success_rearms(self):
        failures = [outcome() for _ in range(3)]
        self.apply(failures)
        for row in [outcome('QUEUED'), outcome('API_SUCCESS', terminal=False),
                    dict(kind='API_SUCCESS', terminal=True, token_count=123)]:
            self.assertTrue(self.apply([row])['suppress'])
        self.assertFalse(self.apply([outcome('API_SUCCESS')])['suppress'])
        self.assertEqual(self.state['provider_circuit']['consecutive_failures'], 0)
        self.assertFalse(self.apply([outcome(), outcome()])['suppress'])
        self.assertTrue(self.apply([outcome()])['suppress'])

    def test_same_class_failures_only_and_unknown_error_never_rearms_open(self):
        self.apply([outcome(), outcome()])
        self.assertFalse(self.apply([outcome(error=dict(message='rate limit exceeded'))])['suppress'])
        self.assertEqual(self.state['provider_circuit']['consecutive_failures'], 1)
        self.assertFalse(self.apply([outcome()])['suppress'])
        self.apply([outcome(), outcome()])
        self.assertTrue(self.apply([outcome(error=dict(message='unclassified'))])['suppress'])

    def test_original_endpoint_change_refuses_without_replacing_original_state(self):
        self.apply([outcome()])
        old = copy.deepcopy(self.state)
        changed = copy.deepcopy(self.bound)
        changed['session_id'] = str(uuid.uuid4())
        gate = circuit.apply_observation(self.state, changed, observation(changed, [outcome('API_SUCCESS')]))
        self.assertTrue(gate['suppress'])
        self.assertEqual(gate['status'], 'PROVIDER_TARGET_CHANGED')
        self.assertEqual(self.state, old)

    def test_nested_actual_error_recognized_without_treating_prose_http_as_status(self):
        self.assertEqual(circuit.classify(dict(message=json.dumps(dict(error=AZURE)))), 'AZURE_DIFFERENT_RESOURCE')
        self.assertIsNone(circuit.classify(dict(message='quoted HTTP 503 status')))
        self.assertIsNone(circuit.classify(dict(message='queued input without API result')))
        self.assertTrue(self.apply([outcome(error=dict(message='API key is disabled'))])['suppress'])


class NativeObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = NativeFixture.attach(self)
        self.text = 'STATUS: provider observation only'
        self.bound = native.bind_target(bridge, 'target', self.text)

    def test_native_metadata_reads_only_and_returns_terminal_outcomes_in_original_order(self):
        older, newer, active = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        client = Mock()
        client.request.side_effect = [dict(thread=dict(id=self.fixture.session_id,
            path=str(self.fixture.transcript))), dict(data=[
                dict(id=active, status='inProgress', error=None),
                dict(id=newer, status='failed', error=AZURE),
                dict(id=older, status='completed', error=None)])]
        result = direct.read_terminal_outcomes(client, self.bound, self.text)
        self.assertEqual([row['attempt_id'] for row in result['outcomes']], [older, newer])
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['thread/read', 'thread/turns/list'])
        self.assertEqual(result['input_operations'], 0)

    def test_native_metadata_path_or_id_change_refuses(self):
        client = Mock()
        client.request.return_value = dict(thread=dict(id=self.fixture.session_id, path='/other'))
        with self.assertRaisesRegex(native.NativeDeliveryError, 'THREAD_CHANGED'):
            direct.read_terminal_outcomes(client, self.bound, self.text)
        self.assertEqual(client.request.call_count, 1)

    def test_transcript_token_metadata_and_incomplete_records_are_not_outcomes(self):
        with self.fixture.transcript.open('ab') as h:
            h.write(json.dumps(dict(type='event_msg', payload=dict(type='token_count', error=AZURE))).encode()+b'\n')
            h.write(json.dumps(dict(type='event_msg', payload=dict(type='task_complete',
                turn_id=str(uuid.uuid4()), error=AZURE))).encode()+b'\n')
            h.write(b'{"type":"event_msg"')
        state = {}
        result = circuit.transcript_outcomes(state, self.bound)
        self.assertEqual(len(result['outcomes']), 1)
        self.assertEqual(result['outcomes'][0]['kind'], 'API_ERROR')
        position = copy.deepcopy(state['provider_transcript_cursor']['position'])
        self.assertEqual(circuit.transcript_outcomes(state, self.bound)['outcomes'], [])
        self.assertEqual(state['provider_transcript_cursor']['position'], position)
        self.fixture.send.assert_not_called()


class DeadTargetTests(unittest.TestCase):
    def test_no_native_agent_and_bound_shell_get_explicit_zero_input_diagnostics(self):
        row = dict(target_surface_uuid='S', workspace_uuid='W', tty='ttys999')
        shell = dict(pid=999, birth=[1,2], executable='/bin/zsh', env=dict(CMUX_SURFACE_ID='S', CMUX_WORKSPACE_ID='W'))
        listing = Mock(stdout='999 '+str(os.getuid())+' ttys999 /bin/zsh\n')
        tty = Mock(stdout='ttys999\n')
        with patch.object(native.subprocess, 'run', side_effect=[listing, tty]), patch.object(native.identity,'process',return_value=shell):
            with self.assertRaisesRegex(native.NativeDeliveryError, 'RETURNED_TO_SHELL') as error:
                native._target_process(row)
        self.assertEqual(error.exception.details['input_operations'], 0)
        self.assertFalse(error.exception.details['automatic_target_selection'])
        with patch.object(native.subprocess, 'run', return_value=Mock(stdout='')):
            with self.assertRaisesRegex(native.NativeDeliveryError, 'AGENT_ABSENT'):
                native._target_process(row)


class CircuitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.stack.enter_context(patch.object(lifecycle, 'ACTIVE_DIR', self.home / 'active'))
        self.caller = '11111111-1111-4111-8111-111111111111'
        self.supervisor = '22222222-2222-4222-8222-222222222222'
        self.workspace = '33333333-3333-4333-8333-333333333333'
        self.bound = binding()
        self.bound['provider'] = 'codex'
        self.bound['schema'] = native.SCHEMA
        self.bound['identity'].update(target_surface_uuid=self.supervisor, workspace_uuid=self.workspace)
        self.events = []
        self.stack.enter_context(patch.object(ready := reask.ready, '_bridge', return_value=bridge))
        self.stack.enter_context(patch.object(native, 'bind_target', return_value=self.bound))
        self.stack.enter_context(patch.object(native, 'require_bound'))
        self.stack.enter_context(patch.object(circuit, 'transcript_outcomes', side_effect=lambda *_:
            observation(self.bound, list(self.events))))
        self.advance = self.stack.enter_context(patch.object(ready, 'advance_ask',
            side_effect=lambda _bridge, _supervisor, _caller, ask, **_kw:
                ask.update(outcome='CONFIRMED', receipt=dict(native_received_at_epoch=1000.))))
        self.stack.enter_context(patch.object(ready, 'write_record'))
        self.stack.enter_context(patch.object(reask, '_held', return_value=(None, '')))
        self.stack.enter_context(patch.object(reask, '_terminal_cadence', return_value=None))
        self.stack.enter_context(patch.object(reask, 'idle_binding', return_value=None))
        self.state = reask.start(self.caller, self.supervisor, 'original-task', workspace=self.workspace, now=1000.)

    def test_three_native_failure_turns_stop_terminal_only_and_actual_success_rearms(self):
        self.events = [outcome() for _ in range(3)]
        first = reask.tick(self.caller, now=1001.)
        self.assertEqual(first['asks'][-1]['outcome'], 'SKIPPED_PROVIDER_CIRCUIT_OPEN')
        self.assertEqual(first['file_posts'], 1)
        self.assertIsNone(first['terminal_pending'])
        self.assertTrue(first['provider_circuit']['state'] == 'OPEN')
        self.advance.assert_not_called()
        second = reask.tick(self.caller, now=1061.)
        self.assertEqual(second['file_posts'], 2)
        self.assertIsNone(second['asks'][-1]['provider_gate']['operator_status'])
        self.events.append(outcome('API_SUCCESS'))
        third = reask.tick(self.caller, now=1121.)
        self.assertEqual(third['asks'][-1]['outcome'], 'CONFIRMED')
        self.assertEqual(third['provider_circuit']['state'], 'CLOSED')
        self.assertEqual(third['file_posts'], 3)
        self.assertEqual(self.advance.call_count, 1)

    def test_unknown_native_observation_cannot_close_previously_open_gate(self):
        self.events = [outcome() for _ in range(3)]
        reask.tick(self.caller, now=1001.)
        with patch.object(circuit, 'transcript_outcomes', side_effect=OSError('unreadable')):
            state = reask.tick(self.caller, now=1061.)
        self.assertEqual(state['provider_circuit']['state'], 'OPEN')
        self.assertEqual(state['file_posts'], 2)
        self.advance.assert_not_called()


if __name__ == '__main__':
    unittest.main()
