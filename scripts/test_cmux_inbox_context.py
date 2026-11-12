import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import cmux_inbox_context as inbox

ROOT = '11111111-1111-4111-8111-111111111111'
WS = '22222222-2222-4222-8222-222222222222'
SURFACE = '33333333-3333-4333-8333-333333333333'
FOREIGN = '44444444-4444-4444-8444-444444444444'
MID = '55555555-5555-4555-8555-555555555555'


def message(mid=MID, **changes):
    return dict(id=mid, body='STATUS: original notice', state='queued',
                recipient_surface_id=SURFACE, recipient_workspace_id=WS, **changes)


class InboxContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.state = self.base / 'cursor'
        self.payload = {'hook_event_name': 'PostToolUse', 'session_id': ROOT}
        self.facts = dict(thread_scope='ROOT_METADATA', actual_thread_id=ROOT)
        self.proof = dict(session=ROOT, foreground=dict(thread_id=ROOT))
        patches = [
            mock.patch.object(inbox.threads, 'describe', return_value=self.facts),
            mock.patch.object(inbox.identity, 'evaluation', side_effect=lambda _: contextlib.nullcontext()),
            mock.patch.object(inbox.identity, 'identity', return_value=(WS, SURFACE)),
            mock.patch.object(inbox.identity, 'resolve', return_value=(WS, SURFACE)),
            mock.patch.object(inbox.identity, 'native_proof', return_value=self.proof),
        ]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.query = mock.Mock(return_value={'messages': [message()]})
        self.emit = mock.Mock()

    def discover(self, **kwargs):
        return inbox.discover(self.payload, {(ROOT, WS, SURFACE)}, self.state,
                              query=self.query, emit=self.emit, **kwargs)

    def test_mixed_outbound_and_inbound_only_exposes_recipient(self):
        outbound = message(FOREIGN)
        outbound.update(recipient_surface_id=FOREIGN, sender_surface_id=SURFACE)
        self.query.return_value['messages'].insert(0, outbound)
        result = self.discover()
        self.assertEqual([r['id'] for r in result], [MID])
        self.assertNotIn(FOREIGN, self.emit.call_args.args[0])

    def test_foreign_workspace_not_exposed(self):
        self.query.return_value['messages'][0]['recipient_workspace_id'] = FOREIGN
        self.assertEqual(self.discover(), [])
        self.emit.assert_not_called()

    def test_child_cannot_query_root_inbox(self):
        self.facts['thread_scope'] = 'CHILD_METADATA'
        self.assertEqual(self.discover(), [])
        self.query.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_metadata_without_native_proof_cannot_query(self):
        self.mocks[4].return_value = None
        self.assertEqual(self.discover(), [])
        self.query.assert_not_called()

    def test_foreground_mismatch_cannot_query(self):
        self.proof['foreground']['thread_id'] = FOREIGN
        self.assertEqual(self.discover(), [])
        self.query.assert_not_called()

    def test_unenrolled_actor_cannot_query(self):
        result = inbox.discover(self.payload, {(FOREIGN, WS, SURFACE)}, self.state,
                                query=self.query, emit=self.emit)
        self.assertEqual(result, [])
        self.query.assert_not_called()

    def test_stop_never_queries_or_emits(self):
        self.payload['hook_event_name'] = 'Stop'
        self.assertEqual(self.discover(), [])
        self.query.assert_not_called()
        self.emit.assert_not_called()

    def test_body_exposure_is_not_claim_or_acceptance(self):
        self.discover()
        text = self.emit.call_args.args[0]
        self.assertIn('not a native user receipt', text)
        self.assertIn('states are unchanged', text)
        self.assertIn('not new authority', text)

    def test_query_is_exact_read_only_cli(self):
        with mock.patch.object(inbox, 'bounded_output', return_value=b'{"messages": []}') as run:
            self.assertEqual(inbox.query_inbox(SURFACE), {'messages': []})
        self.assertEqual(run.call_args.args[0], [inbox.CMUX, 'agent', 'inbox', '--surface', SURFACE,
                                               '--state', 'queued', '--limit', '256', '--json'])

    def test_actual_pipe_output_overflow_refused(self):
        with self.assertRaises(ValueError):
            inbox.bounded_output([sys.executable, '-c', 'print("x" * 10000)'], 1024, 2)

    def test_actual_pipe_timeout_reaps_query(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            inbox.bounded_output([sys.executable, '-c', 'import time;time.sleep(3)'], 1024, 0.05)

    def test_actual_pipe_success(self):
        self.assertEqual(inbox.bounded_output([sys.executable, '-c', 'print("ok")'], 1024, 2), b'ok\n')

    def test_repeated_hook_deduplicates_without_marking_read(self):
        self.assertEqual(len(self.discover()), 1)
        self.assertEqual(self.discover(), [])
        self.assertEqual(self.emit.call_count, 1)
        self.assertEqual(self.query.return_value['messages'][0]['state'], 'queued')

    def test_failed_output_flush_does_not_consume(self):
        self.emit.side_effect = BrokenPipeError('closed output')
        with self.assertRaises(BrokenPipeError):
            self.discover()
        self.emit.side_effect = None
        self.assertEqual(len(self.discover()), 1)

    def test_actor_change_during_host_query_does_not_emit(self):
        self.mocks[3].return_value = (WS, FOREIGN)
        self.assertEqual(self.discover(), [])
        self.emit.assert_not_called()

    def test_native_proof_lost_after_query_does_not_emit(self):
        self.mocks[4].side_effect = [self.proof, None]
        self.assertEqual(self.discover(), [])
        self.emit.assert_not_called()

    def test_changed_original_body_exposed_with_new_hash(self):
        self.discover()
        self.query.return_value['messages'][0]['body'] = 'STATUS: changed body'
        result = self.discover()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['body_sha256'], hashlib.sha256(b'STATUS: changed body').hexdigest())

    def test_large_body_is_explicitly_omitted(self):
        self.query.return_value['messages'][0]['body'] = '中' * 2000
        result = self.discover()
        self.assertEqual(result[0]['body_bytes'], 6000)
        self.assertNotIn('body', result[0])
        self.assertIn('body_omitted', result[0])

    def test_more_than_eight_messages_progress_without_repeat(self):
        self.query.return_value['messages'] = [message(str(inbox.uuid.uuid4())) for _ in range(10)]
        self.assertEqual(len(self.discover()), 8)
        self.assertEqual(len(self.discover()), 2)
        self.assertEqual(self.discover(), [])

    def test_escape_heavy_body_does_not_starve_later_notice(self):
        body = '\x01' * 4000
        self.query.return_value['messages'] = [dict(message(), body=body), message(FOREIGN)]
        result = self.discover()
        self.assertEqual([row['id'] for row in result], [MID, FOREIGN])
        self.assertNotIn('body', result[0])
        self.assertIn('body_omitted', result[0])
        self.assertEqual(result[0]['body_bytes'], 4000)
        self.assertEqual(result[0]['body_sha256'], hashlib.sha256(body.encode()).hexdigest())
        self.assertEqual(result[1]['body'], 'STATUS: original notice')
        self.assertLessEqual(len(self.emit.call_args.args[0].encode()), inbox.MAX_CONTEXT)
        self.assertEqual(self.discover(), [])
        self.assertEqual(self.emit.call_count, 1)

    def test_context_byte_budget_is_bounded(self):
        self.query.return_value['messages'] = [dict(message(str(inbox.uuid.uuid4())), body='中' * 1300) for _ in range(8)]
        self.discover()
        self.assertLessEqual(len(self.emit.call_args.args[0].encode()), inbox.MAX_CONTEXT)

    def test_ambiguous_duplicate_id_refused(self):
        self.query.return_value['messages'].append(message())
        with self.assertRaises(ValueError):
            self.discover()
        self.emit.assert_not_called()

    def test_private_cursor_symlink_refused(self):
        self.state.symlink_to(Path(self.tmp.name))
        with self.assertRaises(ValueError):
            self.discover()
        self.emit.assert_not_called()

    def test_invalid_cursor_preserved(self):
        self.discover()
        p = next(self.state.glob('*.json'))
        p.write_bytes(b'{broken')
        with self.assertRaises(ValueError):
            self.discover()
        self.assertEqual(p.read_bytes(), b'{broken')

    def test_policy_requires_exact_sha_and_private_file(self):
        p = self.base / 'policy.json'
        p.write_text(json.dumps({'schema': 'cmux-inbox-context-policy-v1', 'actors': [
            dict(session_id=ROOT, workspace_uuid=WS, surface_uuid=SURFACE)]}))
        p.chmod(0o600)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(inbox.read_policy(p, digest), {(ROOT, WS, SURFACE)})
        with self.assertRaises(ValueError):
            inbox.read_policy(p, '0' * 64)
        p.chmod(0o644)
        with self.assertRaises(ValueError):
            inbox.read_policy(p, digest)

    def test_duplicate_json_key_refused(self):
        with self.assertRaises(ValueError):
            inbox.strict_json('{"a": 1, "a": 2}')

    def test_output_has_only_posttooluse_additional_context(self):
        with mock.patch('builtins.print') as output:
            inbox.emit_context('notice')
        self.assertEqual(json.loads(output.call_args.args[0]), {
            'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': 'notice'}})
        self.assertTrue(output.call_args.kwargs['flush'])


if __name__ == '__main__':
    unittest.main()
