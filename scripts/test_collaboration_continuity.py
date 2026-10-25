import copy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import collaboration_continuity as c


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ids = [f'{i:08d}-1111-4111-8111-111111111111' for i in (1, 2, 3)]
        self.value = dict(schema=c.SCHEMA, task_id='original',
            workspace_uuid='99999999-1111-4111-8111-111111111111',
            automatic_fallback_authorized=True, authorization=self.pin('authorization'),
            max_subagents=2, observations=[], participants=[])
        for i, role in enumerate(('supervisor', 'executor', 'executor')):
            self.value['participants'].append(dict(role=role, surface_uuid=self.ids[i],
                session_id=self.ids[i], task_id='original', workspace_uuid=self.value['workspace_uuid']))

    def pin(self, text):
        raw = text.encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root / (digest + '.txt')
        path.write_bytes(raw)
        return dict(path=str(path), sha256=digest)

    def event(self, peer, kind='API_SUCCESS', at=1, status=None):
        obs = dict(task_id='original', surface_uuid=self.ids[peer], session_id=self.ids[peer],
            attempt_id=f'{peer}-{at}', kind=kind, terminal=True, at_epoch=at)
        if status is not None:
            obs['http_status'] = status
        obs['evidence'] = self.pin(json.dumps(obs, sort_keys=True))
        self.value['observations'].append(obs)
        return obs

    def outage(self, peer):
        for at in (10, 70, 130):
            self.event(peer, 'API_ERROR', at, 502)

    def native_failure(self, peer=0, at=10):
        obs = self.event(peer, 'API_ERROR', at)
        record = dict(type='event_msg', timestamp=datetime.fromtimestamp(at, timezone.utc).isoformat(),
            payload=dict(type='task_complete', turn_id=obs['attempt_id'], error=dict(
                codex_error_info='other', message=json.dumps(dict(error=dict(
                    type='invalid_request_error', code=None,
                    message='The requested item was created under a different Azure OpenAI resource. '
                            'Use the same resource that created the item to access it. [trace_id=test]'))))))
        obs['evidence'] = self.pin(json.dumps(record))
        return obs, record

    def test_native_nonhttp_request_failure_selects_original_peer_mode(self):
        for peer in range(3):
            self.event(peer)
        for at in (10, 11, 12, 13):
            self.native_failure(at=at)
        result = c.plan(self.value, now=14)
        root = result['health'][self.ids[0]]
        self.assertEqual(result['mode'], 'CLAUDE_PEER')
        self.assertEqual(root['state'], 'UNAVAILABLE')
        self.assertEqual(root['consecutive_failures'], 4)
        self.assertEqual(root['failure_category'], 'REQUEST_CONFIGURATION')
        self.assertIsNone(root['retry_due_epoch'])
        diagnostic = root['request_configuration_failure']
        self.assertEqual(diagnostic['evidence'], self.value['observations'][-1]['evidence'])
        self.assertIsNone(diagnostic['http_status'])
        self.assertFalse(diagnostic['retry_authorized'])
        self.assertFalse(diagnostic['current_provider_health_proven'])
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(result['resource_releases'], 0)
        self.assertFalse(result['replay_allowed'])

    def test_native_numeric_decode_wrapper_keeps_the_original_error(self):
        obs, record = self.native_failure()
        message = 'failed to decode error response: code of type string, raw: {"error":{' \
                  '"code":0,"message":"The requested item was created under a different Azure OpenAI resource."}}'
        record['payload']['error']['message'] = json.dumps(dict(error=dict(
            type='invalid_request_error', code=None, message=message)))
        obs['evidence'] = self.pin(json.dumps(record))
        result = c.plan(self.value, now=11)['health'][self.ids[0]]
        self.assertEqual(result['state'], 'UNAVAILABLE')
        self.assertEqual(result['request_configuration_failure']['original_error'], record['payload']['error'])
        self.assertIsNone(result['request_configuration_failure']['http_status'])

    def test_request_failure_requires_exact_native_terminal_turn_time_and_scope(self):
        changes = [lambda r: r.update(type='tool_result'),
                   lambda r: r['payload'].update(type='task_started'),
                   lambda r: r['payload'].update(turn_id='foreign-turn'),
                   lambda r: r.update(timestamp='1970-01-01T00:00:11Z'),
                   lambda r: r.update(session_id=self.ids[1]),
                   lambda r: r['payload'].update(task_id='foreign'),
                   lambda r: r['payload']['error'].update(codex_error_info='server_error'),
                   lambda r: r['payload']['error'].update(httpStatusCode=400)]
        for change in changes:
            self.value['observations'] = []
            obs, record = self.native_failure()
            change(record); obs['evidence'] = self.pin(json.dumps(record))
            with self.subTest(change=change):
                root = c.plan(self.value, now=12)['health'][self.ids[0]]
                self.assertEqual(root['state'], 'UNKNOWN')
                self.assertIsNone(root['failure_category'])

    def test_generic_unstructured_and_nonterminal_errors_remain_unknown(self):
        for variant in ('generic', 'unstructured', 'nonterminal', 'http', 'duplicate'):
            self.value['observations'] = []
            obs, record = self.native_failure()
            if variant == 'generic':
                record['payload']['error']['message'] = json.dumps(dict(error=dict(
                    type='invalid_request_error', message='Invalid request.')))
            elif variant == 'unstructured':
                record['payload']['error']['message'] = 'The requested item was created under a different Azure OpenAI resource.'
            elif variant == 'nonterminal':
                obs['terminal'] = False
            elif variant == 'http':
                obs['http_status'] = 400
            raw = json.dumps(record)
            if variant == 'duplicate':
                raw = raw.replace('"type": "event_msg"', '"type": "event_msg", "type": "tool_result"')
            obs['evidence'] = self.pin(raw)
            with self.subTest(variant=variant):
                root = c.plan(self.value, now=12)['health'][self.ids[0]]
                self.assertEqual(root['state'], 'UNKNOWN')
                self.assertEqual(root['consecutive_failures'], 0)

    def test_queue_keeps_request_failure_and_success_resets_it(self):
        self.native_failure()
        self.event(0, 'QUEUED', 11)
        root = c.plan(self.value, now=12)['health'][self.ids[0]]
        self.assertEqual(root['failure_category'], 'REQUEST_CONFIGURATION')
        self.event(0, at=13)
        root = c.plan(self.value, now=14)['health'][self.ids[0]]
        self.assertEqual(root['state'], 'AVAILABLE')
        self.assertEqual(root['consecutive_failures'], 0)
        self.assertIsNone(root['request_configuration_failure'])

    def test_later_http_outage_has_its_own_retry_count(self):
        self.native_failure()
        self.event(0, 'API_ERROR', 11, 502)
        root = c.plan(self.value, now=12)['health'][self.ids[0]]
        self.assertEqual(root['state'], 'RETRYING')
        self.assertEqual(root['consecutive_failures'], 1)
        self.assertEqual(root['retry_due_epoch'], 71)
        self.assertIsNone(root['request_configuration_failure'])

    def test_cli_uses_pinned_native_error_and_does_not_mutate_the_plan(self):
        self.native_failure()
        path = self.root / 'PLAN.json'; path.write_text(json.dumps(self.value))
        before = path.read_bytes(); output = io.StringIO()
        with redirect_stdout(output):
            c.main(['--input', str(path)])
        result = json.loads(output.getvalue())
        self.assertEqual(result['health'][self.ids[0]]['failure_category'], 'REQUEST_CONFIGURATION')
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(result['resource_releases'], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_all_three_available_then_each_failure_topology(self):
        for peer in range(3):
            self.event(peer)
        self.assertEqual(c.plan(self.value, now=150)['mode'], 'TRIO')
        self.outage(1)
        self.assertEqual(c.plan(self.value, now=150)['mode'], 'CODEX_CLAUDE')
        self.outage(2)
        result = c.plan(self.value, now=150)
        self.assertEqual(result['mode'], 'CODEX_SOLO')
        self.assertEqual(result['resource_releases'], 0)
        self.assertFalse(result['replacement_session_allowed'])
        for peer in (1, 2):
            self.event(peer, at=200)
        self.outage(0)
        self.assertEqual(c.plan(self.value, now=210)['mode'], 'CLAUDE_PEER')

    def test_queue_silence_and_unknown_are_not_api_failures(self):
        for peer in range(3):
            self.event(peer)
        for at in (10, 70, 130):
            self.event(0, 'NO_REPLY', at)
        result = c.plan(self.value, now=150)
        self.assertEqual(result['mode'], 'TRIO')
        self.assertEqual(result['health'][self.ids[0]]['consecutive_failures'], 0)

    def test_queue_does_not_erase_terminal_failures_or_invent_health(self):
        self.event(0, 'QUEUED', 1)
        self.assertEqual(c.plan(self.value, now=2)['health'][self.ids[0]]['state'], 'UNKNOWN')
        for at in (10, 70, 130):
            self.event(0, 'API_ERROR', at, 502)
            self.event(0, 'QUEUED', at+1)
        result = c.plan(self.value, now=150)
        self.assertEqual(result['health'][self.ids[0]]['state'], 'UNAVAILABLE')
        with self.assertRaises(ValueError):
            c.plan(self.value, now=float('nan'))

    def test_success_resets_failure_episode_and_retries_are_bounded(self):
        self.event(0)
        self.event(1, 'API_ERROR', 10, 502)
        self.event(1, at=20)
        self.event(1, 'API_ERROR', 30, 502)
        result = c.plan(self.value, now=40)
        self.assertEqual(result['health'][self.ids[1]]['consecutive_failures'], 1)
        self.assertEqual(result['health'][self.ids[1]]['retry_due_epoch'], 90)
        self.assertEqual(result['mode'], 'CODEX_CONTINUE_INDEPENDENT')

    def test_original_attempt_cannot_be_counted_twice_or_before_retry_after(self):
        obs = self.event(0, 'API_ERROR', 10, 502)
        self.value['observations'].append(copy.deepcopy(obs))
        with self.assertRaisesRegex(ValueError, 'multiple attempts'):
            c.plan(self.value, now=100)
        self.value['observations'].pop()
        self.event(0, 'API_ERROR', 20, 502)
        with self.assertRaisesRegex(ValueError, 'retry-after'):
            c.plan(self.value, now=100)

    def test_foreign_session_changed_evidence_and_future_event_refused(self):
        obs = self.event(0)
        obs['session_id'] = self.ids[1]
        with self.assertRaises(ValueError):
            c.plan(self.value, now=100)
        obs['session_id'] = self.ids[0]
        with self.assertRaises(ValueError):
            c.plan(self.value, now=0)
        Path(obs['evidence']['path']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            c.plan(self.value, now=100)


if __name__ == '__main__':
    unittest.main()
