"""Original-request crash recovery, finite discovery and no duplicate input."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import executor_ready as ready
import test_executor_ready as fixtures


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.FiniteReadyTests.setUp
    run_loop = fixtures.FiniteReadyTests.run_loop
    ask = fixtures.FiniteReadyTests.ask

    def foreign(self, count):
        root = ready.requests_dir()
        root.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (root / f'foreign-{index:04}.json').write_text(
                json.dumps(dict(caller_surface_uuid='another-caller')))
        return root

    def pending(self):
        record = self.run_loop(one_shot=True)
        marker = ready.PREFIX + '_0123456789abcdef'
        record['pending_request'] = dict(version=1, marker=marker)
        ready.write_json(ready.loop_path(self.caller), record)
        return record, marker

    def original(self, marker, record, **overrides):
        text = ready.build_text('surface:2', marker)
        path, _ = ready.write_record(self.caller, marker, text, 'surface:1',
                                     'task-x', record['episode_id'])
        if overrides:
            body = ready.read_json(path)
            body.update(overrides)
            ready.write_json(path, body)
        return path

    def test_300_unrelated_records_do_not_prevent_one_authorized_ask(self):
        self.foreign(300)
        record = self.ask()
        self.assertEqual(record['ask_count'], 1)
        self.assertEqual(len(self.bridge.submitted), 1)
        self.assertEqual(record['request_recovery']['entries'], 300)
        self.assertEqual(record['request_recovery']['verification_entries'], 300)

    def test_large_legacy_orphan_is_adopted_without_input(self):
        self.foreign(300)
        orphan = ready.prepare_ask(self.bridge, 'surface:1', self.caller,
                                   'surface:2', 'task-x')
        record = self.ask()
        self.assertEqual(record['asks'][0]['marker'], orphan['marker'])
        self.assertEqual(self.bridge.submitted, [])

    def test_exact_pending_request_never_enumerates_unrelated_history(self):
        record, marker = self.pending()
        self.original(marker, record)
        self.foreign(300)
        with patch.object(ready.os, 'scandir', side_effect=AssertionError('no scan')):
            resumed = self.ask()
        self.assertEqual(resumed['asks'][0]['marker'], marker)
        self.assertEqual(self.bridge.submitted, [])
        self.assertNotIn('pending_request', resumed)

    def test_intent_is_durable_before_request_creation_and_input(self):
        original = ready.prepare_ask
        seen = []
        def prepare(*args, **kwargs):
            record = ready.read_json(ready.loop_path(self.caller))
            self.assertEqual(record['pending_request']['marker'], kwargs['marker'])
            self.assertEqual(record['asks'], [])
            self.assertEqual(self.bridge.submitted, [])
            seen.append(kwargs['marker'])
            return original(*args, **kwargs)
        with patch.object(ready, 'prepare_ask', side_effect=prepare):
            record = self.ask()
        persisted = ready.read_json(ready.loop_path(self.caller))
        self.assertEqual(record['asks'][0]['marker'], seen[0])
        self.assertNotIn('pending_request', persisted)
        self.assertEqual(len(self.bridge.submitted), 1)

    def test_crash_before_creation_resumes_same_marker_only_on_explicit_ask(self):
        with patch.object(ready, 'prepare_ask', side_effect=RuntimeError('crash before create')):
            interrupted = self.ask()
        marker = interrupted['pending_request']['marker']
        passive = self.run_loop(one_shot=True)
        self.assertEqual(passive['asks'], [])
        self.assertEqual(self.bridge.submitted, [])
        resumed = self.ask()
        self.assertEqual(resumed['asks'][0]['marker'], marker)
        self.ask()
        self.assertEqual(sum(not call['reconcile_only'] for call in self.bridge.submitted), 1)

    def test_crash_after_creation_cannot_send_or_change_marker(self):
        original = ready.prepare_ask
        def prepare(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('crash after create')
        with patch.object(ready, 'prepare_ask', side_effect=prepare):
            interrupted = self.ask()
        marker = interrupted['pending_request']['marker']
        resumed = self.ask()
        self.assertEqual(resumed['asks'][0]['marker'], marker)
        self.assertEqual(self.bridge.submitted, [])

    def test_wrong_task_or_episode_in_indexed_request_is_refused(self):
        for changed in (dict(task_id='other'), dict(episode_id='other')):
            with self.subTest(changed=changed):
                self.setUp()
                record, marker = self.pending()
                self.original(marker, record, **changed)
                refused = self.ask()
                self.assertIn('UNRESOLVED_IDLE_EPISODE', refused['last_error'])
                self.assertEqual(refused['asks'], [])
                self.assertEqual(self.bridge.submitted, [])

    def test_malformed_intent_or_request_never_creates_input(self):
        record, marker = self.pending()
        record['pending_request']['marker'] = 'invalid'
        ready.write_json(ready.loop_path(self.caller), record)
        refused = self.ask()
        self.assertIn('PENDING_REQUEST_INTENT_INVALID', refused['last_error'])
        self.assertEqual(self.bridge.submitted, [])
        record['pending_request']['marker'] = marker
        ready.write_json(ready.loop_path(self.caller), record)
        path = ready.requests_dir() / (ready.request_key(self.caller, marker) + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{broken')
        refused = self.ask()
        self.assertIn('ORIGINAL_REQUEST_REQUIRED', refused['last_error'])
        self.assertEqual(self.bridge.submitted, [])

    def test_unreadable_record_beyond_old_128_budget_is_still_refused(self):
        root = self.foreign(300)
        (root / 'malformed.json').write_text('{broken')
        refused = self.ask()
        self.assertIn('REQUEST_RECORD_UNREADABLE', refused['last_error'])
        self.assertEqual(refused['asks'], [])
        self.assertEqual(self.bridge.submitted, [])

    def test_multiple_legacy_originals_never_authorize_input(self):
        self.foreign(300)
        ready.prepare_ask(self.bridge, 'surface:1', self.caller, 'surface:2', 'task-x')
        ready.prepare_ask(self.bridge, 'surface:1', self.caller, 'surface:2', 'task-x')
        refused = self.ask()
        self.assertIn('MULTIPLE_ORIGINAL_REQUESTS', refused['last_error'])
        self.assertEqual(self.bridge.submitted, [])

    def test_changed_directory_during_scan_cannot_establish_absence(self):
        root = self.foreign(300)
        checkpoints = []
        def checkpoint(progress):
            checkpoints.append(copy.deepcopy(progress))
            if progress['entries'] == 128 and progress['verification_entries'] == 0:
                (root / 'new-file.json').write_text(json.dumps(dict(caller_surface_uuid='other')))
        with self.assertRaisesRegex(ValueError, 'REQUEST_RECOVERY_CHANGED'):
            ready._orphan_request(self.caller, 'surface:1', 'task-x', checkpoint=checkpoint)
        self.assertTrue(checkpoints)
        self.assertEqual(self.bridge.submitted, [])

    def test_in_place_changed_record_is_detected_by_second_metadata_pass(self):
        root = self.foreign(5)
        original = ready.read_json
        visited = []
        def read(path):
            value = original(path)
            visited.append(Path(path))
            if len(visited) == 5:
                visited[0].write_text(json.dumps(dict(caller_surface_uuid=self.caller,
                                                     target_surface='surface:1', task_id='task-x')))
            return value
        with patch.object(ready, 'read_json', side_effect=read):
            with self.assertRaisesRegex(ValueError, 'REQUEST_RECOVERY_CHANGED'):
                ready._orphan_request(self.caller, 'surface:1', 'task-x')
        self.assertEqual(self.bridge.submitted, [])

    def test_incomplete_discovery_keeps_durable_diagnostics_and_never_sends(self):
        self.foreign(300)
        with patch.object(ready, 'LEGACY_RECOVERY_SECONDS', 0.0):
            refused = self.ask()
        persisted = ready.read_json(ready.loop_path(self.caller))
        self.assertIn('REQUEST_RECOVERY_INCOMPLETE', refused['last_error'])
        self.assertEqual(persisted['request_recovery']['status'], 'INCOMPLETE_RESTART_REQUIRED')
        self.assertEqual(persisted['asks'], [])
        self.assertEqual(self.bridge.submitted, [])

    def test_symlink_request_and_symlink_directory_are_refused(self):
        root = self.foreign(1)
        (root / 'linked.json').symlink_to(root / 'foreign-0000.json')
        refused = self.ask()
        self.assertIn('REQUEST_RECORD_UNREADABLE', refused['last_error'])
        self.assertEqual(self.bridge.submitted, [])
        (root / 'linked.json').unlink()
        moved = root.with_name('original-requests')
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
        refused = self.ask()
        self.assertIn('REQUEST_DIRECTORY_UNREADABLE', refused['last_error'])
        self.assertEqual(self.bridge.submitted, [])

    def test_request_name_is_synced_before_the_send_opportunity(self):
        synced = []
        original = ready.sync_directory
        def sync(path):
            if Path(path) == ready.requests_dir():
                self.assertEqual(self.bridge.submitted, [])
                synced.append(path)
            return original(path)
        with patch.object(ready, 'sync_directory', side_effect=sync):
            self.ask()
        self.assertEqual(len(synced), 1)


if __name__ == '__main__':
    unittest.main()
