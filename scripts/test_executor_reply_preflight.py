"""Read-only reply diagnostics preserve issuance, consumption and exact inputs."""
import copy
import hashlib
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

import executor_reask as reask
import executor_reply as replies
import executor_reply_preflight as preflight


class ReplyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.state = dict(caller_surface_uuid='executor', supervisor='supervisor',
                          task_id='task', episode_id='episode', state=reask.WAITING,
                          attempt_count=4, marker_secret='original-secret', asks=[])
        self.marker = reask.issue_marker(self.state, 2)
        self.body = dict(marker=self.marker, caller_surface_uuid='executor',
                         supervisor_uuid='supervisor', task_id='task', episode_id='episode',
                         status='WAITING_DEPENDENCY', trigger='Wait for the pinned review candidate')

    def diagnose(self, state=None, body=None):
        state = self.state if state is None else state
        body = self.body if body is None else body
        before = copy.deepcopy((state, body))
        result = preflight.diagnose(state, body)
        self.assertEqual((state, body), before)
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(result['state_changes'], 0)
        self.assertFalse(result['reply_consumed'])
        return result

    def test_late_signed_noncontinuing_marker_outside_history_is_valid(self):
        self.assertEqual(self.diagnose()['outcome'], 'REPLY_VALID_FOR_ORIGINAL_EPISODE')

    def test_unissued_future_or_tampered_markers_refused_without_history_bypass(self):
        for marker in (reask.issue_marker(self.state, 5), self.marker + 'a', 'not-issued'):
            with self.subTest(marker=marker):
                state = dict(self.state, asks=[dict(marker=marker)])
                self.assertEqual(self.diagnose(state, dict(self.body, marker=marker))['reason'],
                                 'UNISSUED_OR_CONSUMED_MARKER')

    def test_legacy_noncontinuing_requires_exact_original_ask(self):
        state = dict(self.state, marker_secret=None, asks=[dict(marker='legacy_marker')])
        self.assertEqual(self.diagnose(state, dict(self.body, marker='legacy_marker'))['outcome'],
                         'REPLY_VALID_FOR_ORIGINAL_EPISODE')
        self.assertEqual(self.diagnose(state)['reason'], 'UNISSUED_OR_CONSUMED_MARKER')

    def test_answered_consumed_or_stopped_episodes_never_reopen(self):
        for value in (reask.ANSWERED, reask.CONSUMED, reask.STOPPED):
            with self.subTest(state=value):
                self.assertEqual(self.diagnose(dict(self.state, state=value))['reason'],
                                 'ORIGINAL_EPISODE_NOT_WAITING')

    def continuing(self, **overrides):
        return dict(self.state, continuation=dict(enabled=True, status='ACTIVE'),
                    replies_closed_through=1, **overrides)

    def wait_body(self, **overrides):
        return dict(self.body, owner='supervisor', next_due_epoch=100, deadline_epoch=200,
                    **overrides)

    def test_continuing_reply_requires_signed_open_frontier(self):
        body = self.wait_body()
        self.assertEqual(self.diagnose(self.continuing(), body)['outcome'],
                         'REPLY_VALID_FOR_ORIGINAL_EPISODE')
        closed = dict(self.continuing(), replies_closed_through=2)
        self.assertEqual(self.diagnose(closed, body)['reason'], 'UNISSUED_OR_CONSUMED_MARKER')

    def test_wait_owner_and_finite_ordered_deadline_required(self):
        body = self.wait_body()
        for changes in ({'owner':'other'}, {'next_due_epoch':True}, {'deadline_epoch':0},
                        {'next_due_epoch':300}, {'deadline_epoch':float('inf')}):
            with self.subTest(changes=changes):
                result = self.diagnose(self.continuing(), dict(body, **changes))
                self.assertEqual(result['reason'], 'CONTINUING_WAIT_OWNER_OR_DEADLINE_INVALID')

    def test_inactive_or_unsigned_continuing_authority_refused(self):
        for changes in ({'marker_secret':None}, {'continuation':{'enabled':True,'status':'STOPPED'}},
                        {'continuation':{'enabled':False,'status':'ACTIVE'}}):
            with self.subTest(changes=changes):
                self.assertEqual(self.diagnose(dict(self.continuing(), **changes))['reason'],
                                 'CONTINUING_AUTHORITY_INACTIVE')

    def test_unsupported_status_gives_concrete_wait_guidance_without_new_status(self):
        for status in ('STANDBY', 'HOLD', 'WAIT', [], {}):
            with self.subTest(status=status):
                body = dict(self.body, status=status)
                result = self.diagnose(body=body)
                self.assertEqual(result['reason'], 'REPLY_STATUS_UNSUPPORTED')
                self.assertFalse(replies.valid(body, [self.marker], caller='executor',
                                               supervisor='supervisor', task_id='task',
                                               episode_id='episode'))
        self.assertIn('WAITING_DEPENDENCY', self.diagnose(body=dict(self.body, status='STANDBY'))['suggestion'])

    def test_exact_binding_trigger_and_queue_refusals(self):
        for changes, reason in (({'task_id':'other'}, 'REPLY_BINDING_MISMATCH:task_id'),
                                ({'episode_id':'other'}, 'REPLY_BINDING_MISMATCH:episode_id'),
                                ({'supervisor_uuid':'other'}, 'REPLY_BINDING_MISMATCH:supervisor_uuid'),
                                ({'trigger':'REPLACE_WITH_CONCRETE_TRIGGER'}, 'CONCRETE_TRIGGER_REQUIRED'),
                                ({'queued':True}, 'QUEUED_OR_UNRECEIVED_REPLY')):
            with self.subTest(changes=changes):
                self.assertEqual(self.diagnose(body=dict(self.body, **changes))['reason'], reason)

    def test_malformed_state_and_body_have_bounded_refusals(self):
        for state in ([], {}, dict(self.state, asks={}), dict(self.state, asks=[0]),
                      dict(self.state, attempt_count=True), dict(self.state, continuation=[]),
                      dict(self.state, replies_closed_through=5), dict(self.state, marker_secret=0)):
            with self.subTest(state=state):
                self.assertEqual(self.diagnose(state=state)['outcome'], 'REPLY_REFUSED')
        for body in ([], {}, dict(self.body, marker=[]), dict(self.body, marker='../unsafe')):
            with self.subTest(body=body):
                self.assertEqual(self.diagnose(body=body)['outcome'], 'REPLY_REFUSED')

    def files(self, folder):
        state, body = folder/'state.json', folder/'candidate.json'
        state.write_text(json.dumps(self.state))
        body.write_text(json.dumps(self.body))
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        return state, sha(state), body, sha(body)

    def test_cli_validates_pins_and_writes_no_files(self):
        with tempfile.TemporaryDirectory() as folder:
            state, sha, body, bsha = self.files(Path(folder))
            before = {p.name:p.read_bytes() for p in Path(folder).iterdir()}
            output = io.StringIO()
            with redirect_stdout(output):
                code = preflight.main(['--state-file',str(state),'--state-sha256',sha,
                                       '--body-file',str(body),'--body-sha256',bsha])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['candidate']['sha256'], bsha)
            self.assertEqual({p.name:p.read_bytes() for p in Path(folder).iterdir()}, before)

    def test_duplicate_keys_nonobject_invalid_utf8_and_pin_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'candidate.json'
            for raw in (b'{"status":"ACK","status":"STANDBY"}', b'[]', b'\xff',
                        b'{"status":NaN}', '{}'.encode('utf-16')):
                path.write_bytes(raw)
                with self.assertRaises((ValueError, UnicodeDecodeError)):
                    preflight.read_object(path, hashlib.sha256(raw).hexdigest())
            path.write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'PIN_MISMATCH'):
                preflight.read_object(path, '0'*64)
            with self.assertRaisesRegex(ValueError, 'EXACT_SHA256_REQUIRED'):
                preflight.read_object(path, 'partial')

    def test_same_content_inode_replacement_between_reads_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.files(Path(folder))
            original = preflight.read_object
            calls = []
            def replace_after_candidate(path, expected):
                result = original(path, expected)
                calls.append(str(path))
                if len(calls) == 2:
                    replacement = Path(folder)/'replacement.json'
                    replacement.write_bytes(args[0].read_bytes())
                    replacement.replace(args[0])
                return result
            with patch.object(preflight, 'read_object', side_effect=replace_after_candidate):
                with self.assertRaisesRegex(ValueError, 'INPUT_IDENTITY_CHANGED'):
                    preflight.preflight(*args)

    def test_cli_deep_json_returns_bounded_refusal_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            args = self.files(root)
            nested = b'{"nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'
            for target in (args[0], args[2]):
                with self.subTest(target=target.name):
                    self.files(root)
                    target.write_bytes(nested)
                    before = {p.name: p.read_bytes() for p in root.iterdir()}
                    output = io.StringIO()
                    # Python 3.14's accelerated scanner handles deep arrays
                    # iteratively. Exercise the real recursive fallback parser
                    # to verify older/fallback interpreters also return JSON.
                    with patch.object(json.scanner, 'make_scanner', json.scanner.py_make_scanner), \
                            redirect_stdout(output):
                        code = preflight.main([
                            '--state-file', str(args[0]), '--state-sha256',
                            hashlib.sha256(args[0].read_bytes()).hexdigest(),
                            '--body-file', str(args[2]), '--body-sha256',
                            hashlib.sha256(args[2].read_bytes()).hexdigest(),
                        ])
                    result = json.loads(output.getvalue())
                    self.assertEqual(code, 2)
                    self.assertEqual(result['reason'], 'JSON_NESTING_LIMIT_EXCEEDED')
                    self.assertEqual(result['input_operations'], 0)
                    self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_symlink_special_and_changed_contents_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.files(Path(folder))
            link = Path(folder)/'link.json'; link.symlink_to(args[0])
            with self.assertRaises((OSError, ValueError)):
                preflight.read_object(link, args[1])
            args[0].write_text('{}')
            with self.assertRaisesRegex(ValueError, 'PIN_MISMATCH'):
                preflight.preflight(*args)


if __name__ == '__main__':
    unittest.main()
