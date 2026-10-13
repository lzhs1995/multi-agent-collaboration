"""Real nonblocking locks and bounded races; only temporary file state."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
from threading import Event, current_thread, main_thread
import unittest
from unittest.mock import patch

import executor_ready as ready
import executor_reask as reask
import executor_reask_lifecycle as lifecycle
import executor_reask_protocol as protocol
import mac_harness as harness


CALLER = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
SUPERVISOR = '33333333-3333-4333-8333-333333333333'
WORKSPACE = '44444444-4444-4444-8444-444444444444'


class MarkerLockTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix='harness-marker-lock-'))).resolve()
        self.active = self.home / 'active'
        self.root = self.home / 'evidence'
        self.root.mkdir()
        self.evidence = self.root / 'preserved-review.txt'
        self.evidence.write_bytes(b'original task evidence\n')
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(harness, '_ACTIVE_DIR', self.active))
        self.stack.enter_context(patch.object(harness, '_workspace_key', return_value=WORKSPACE))
        self.stack.enter_context(patch.object(ready, 'state_root', return_value=self.home / 'state'))
        self.stack.enter_context(patch('subprocess.run', side_effect=AssertionError('no transport')))
        # This suite owns locking, not the independently changing compatibility
        # policy. Reservation writes and lifecycle/caller locks remain real.
        self.stack.enter_context(patch.object(protocol, 'supports_state', return_value=True))
        auth = self.home / 'authorization.txt'
        auth.write_bytes(b'continuation fixture')
        pin = dict(path=str(auth), sha256=hashlib.sha256(auth.read_bytes()).hexdigest(),
                   bytes=auth.stat().st_size)
        controller = reask.controller()
        for caller in (CALLER, OTHER):
            reask.save(dict(version=3, file_protocol=protocol.descriptor(),
                            controller=copy.deepcopy(controller),
                            caller_surface_uuid=caller, supervisor=SUPERVISOR,
                            workspace_uuid=WORKSPACE, task_id='original-inquiry',
                            episode_id=caller, state=reask.WAITING, asks=[],
                            channels=[], paused_tasks=[], next_due_epoch=1., started_epoch=1.,
                            continuation=dict(enabled=True, id=caller, status='ACTIVE',
                                              overall_task='fixture task', owner=SUPERVISOR,
                                              authorized_epoch=1., until_epoch=None,
                                              authorization=pin, controller=controller)))

    def tearDown(self):
        self.assertEqual(self.evidence.read_bytes(), b'original task evidence\n')

    def arm(self, task='child', callers=(CALLER,)):
        return harness.arm_task(task, self.root, supervisor_surface_uuid=SUPERVISOR,
                                workspace_uuid=WORKSPACE,
                                executors=[dict(surface_uuid=caller) for caller in callers])

    def path(self, marker):
        return self.active / WORKSPACE / (marker['collaboration_id'] + '.json')

    def legacy(self):
        path = self.active / (WORKSPACE + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = dict(marker_version=1, task_id='legacy', artifact_root=str(self.root),
                      last_activity_at='old')
        path.write_text(json.dumps(marker))
        return path

    def assert_paused(self, caller, *markers):
        expected = [dict(collaboration_id=m['collaboration_id'], task_id=m['task_id'])
                    for m in markers]
        self.assertEqual(reask.load(caller)['paused_tasks'], expected)

    def test_arm_and_disarm_hold_every_caller_lock_for_marker_and_reservation(self):
        original = lifecycle.task_boundary_locked
        events = []

        def boundary(marker, event, **kwargs):
            self.assertEqual(json.loads(self.path(marker).read_text()), marker)
            for caller in (CALLER, OTHER):
                with self.assertRaises(BlockingIOError), reask.episode_lock(caller):
                    self.fail('boundary is not protected by the caller lock')
            events.append(event)
            return original(marker, event, **kwargs)

        with patch.object(lifecycle, 'task_boundary_locked', side_effect=boundary), \
                patch.object(lifecycle, 'task_boundary', side_effect=AssertionError('nested locking')):
            marker = self.arm(callers=(OTHER, CALLER))
            self.assert_paused(CALLER, marker)
            self.assert_paused(OTHER, marker)
            self.assertEqual(harness.disarm_task('child'), 1)
        self.assertEqual(events, ['RESERVED', 'RELEASED'])
        self.assertFalse(self.path(marker).exists())
        for caller in (CALLER, OTHER):
            self.assert_paused(caller)
            with reask.episode_lock(caller):
                pass

    def test_arm_busy_is_nonblocking_and_releases_partially_acquired_locks(self):
        with reask.episode_lock(OTHER), \
                patch.object(lifecycle, 'task_boundary_locked') as boundary:
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                self.arm(callers=(OTHER, CALLER))
            boundary.assert_not_called()
            self.assertEqual(list(self.active.rglob('*.json')), [])
            with reask.episode_lock(CALLER):
                pass
        self.assert_paused(CALLER)
        self.assert_paused(OTHER)

    def test_arm_publication_window_excludes_scheduler_and_allows_evidence_write(self):
        entered, finish = Event(), Event()
        original = lifecycle.task_boundary_locked

        def boundary(marker, event, **kwargs):
            if event == 'RESERVED':
                entered.set()
                if not finish.wait(3):
                    raise AssertionError('test did not release arm')
            return original(marker, event, **kwargs)

        with patch.object(lifecycle, 'task_boundary_locked', side_effect=boundary), \
                ThreadPoolExecutor(max_workers=2) as workers:
            armed = workers.submit(self.arm)
            try:
                self.assertTrue(entered.wait(2))
                paths = list((self.active / WORKSPACE).glob('*.json'))
                self.assertEqual(len(paths), 1)
                before = paths[0].read_bytes()
                with self.assertRaises(BlockingIOError), reask.episode_lock(CALLER):
                    self.fail('scheduler entered between marker and reservation')
                receipt = self.root / 'during-arm.json'
                workers.submit(harness._write, receipt, {'complete': True}).result(timeout=1)
                self.assertEqual(json.loads(receipt.read_text()), {'complete': True})
                self.assertEqual(paths[0].read_bytes(), before)
            finally:
                finish.set()
            marker = armed.result(timeout=2)
        self.assert_paused(CALLER, marker)

    def test_reservation_failure_is_loud_and_preserves_formal_evidence(self):
        with patch.object(lifecycle, 'task_boundary_locked', side_effect=OSError('reservation failed')):
            with self.assertRaisesRegex(OSError, 'reservation failed'):
                self.arm()
        paths = list((self.active / WORKSPACE).glob('*.json'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(json.loads(paths[0].read_text())['task_id'], 'child')
        self.assert_paused(CALLER)

    def test_disarm_checks_full_identity_again_after_locking(self):
        original = harness._marker_update_lock
        for field in ('task_id', 'collaboration_id', 'artifact_root', 'workspace_uuid', 'participants'):
            with self.subTest(field=field):
                marker = self.arm()
                path = self.path(marker)
                changed = copy.deepcopy(marker)
                if field == 'participants':
                    changed[field][-1]['surface_uuid'] = OTHER
                else:
                    changed[field] += '-replacement'

                @contextmanager
                def replace_before_lock(path, observed, **kwargs):
                    path.write_text(json.dumps(changed))
                    with original(path, observed, **kwargs):
                        yield

                with patch.object(harness, '_marker_update_lock', replace_before_lock), \
                        patch.object(lifecycle, 'task_boundary_locked') as boundary:
                    with self.assertRaisesRegex(RuntimeError, 'changed'):
                        harness.disarm_task('child')
                    boundary.assert_not_called()
                self.assertEqual(json.loads(path.read_text()), changed)
                self.assert_paused(CALLER, marker)
                path.write_text(json.dumps(marker))
                self.assertEqual(harness.disarm_task('child'), 1)

    def test_disarm_accepts_new_telemetry_and_releases_exact_reservation(self):
        marker = self.arm()
        other = self.arm('other')
        original = harness._marker_update_lock

        @contextmanager
        def beat_before_lock(path, observed, **kwargs):
            value = json.loads(path.read_text())
            value['last_activity_at'] = 'newer-heartbeat'
            path.write_text(json.dumps(value))
            with original(path, observed, **kwargs):
                yield

        with patch.object(harness, '_marker_update_lock', beat_before_lock):
            self.assertEqual(harness.disarm_task('child'), 1)
        self.assertFalse(self.path(marker).exists())
        self.assertEqual(json.loads(self.path(other).read_text()), other)
        self.assert_paused(CALLER, other)

    def test_disarm_of_marker_removed_before_lock_does_not_release_or_overcount(self):
        marker = self.arm()
        original = harness._marker_update_lock

        @contextmanager
        def remove_before_lock(path, observed, **kwargs):
            path.unlink()
            with original(path, observed, **kwargs):
                yield

        with patch.object(harness, '_marker_update_lock', remove_before_lock), \
                patch.object(lifecycle, 'task_boundary_locked') as boundary:
            self.assertEqual(harness.disarm_task('child'), 0)
            boundary.assert_not_called()
        self.assert_paused(CALLER, marker)

    def test_release_failure_preserves_marker_and_reservation(self):
        marker = self.arm()
        before = self.path(marker).read_bytes()
        with patch.object(lifecycle, 'task_boundary_locked', side_effect=OSError('release failed')):
            with self.assertRaisesRegex(OSError, 'release failed'):
                harness.disarm_task('child')
        self.assertEqual(self.path(marker).read_bytes(), before)
        self.assert_paused(CALLER, marker)

    def test_disarm_holds_locks_between_release_and_unlink(self):
        marker = self.arm()
        path = self.path(marker)
        before = path.read_bytes()
        entered, finish = Event(), Event()
        original = Path.unlink

        def pause_unlink(target, *args, **kwargs):
            if target == path:
                entered.set()
                if not finish.wait(3):
                    raise AssertionError('test did not release unlink')
            return original(target, *args, **kwargs)

        with patch.object(Path, 'unlink', pause_unlink), \
                ThreadPoolExecutor(max_workers=2) as workers:
            disarmed = workers.submit(harness.disarm_task, 'child')
            try:
                self.assertTrue(entered.wait(2))
                self.assert_paused(CALLER)
                with self.assertRaises(BlockingIOError), reask.episode_lock(CALLER):
                    self.fail('scheduler entered between release and unlink')
                receipt = self.root / 'during-disarm.json'
                workers.submit(harness._write, receipt, {'complete': True}).result(timeout=1)
                self.assertEqual(json.loads(receipt.read_text()), {'complete': True})
                self.assertEqual(path.read_bytes(), before)
            finally:
                finish.set()
            self.assertEqual(disarmed.result(timeout=2), 1)
        self.assertFalse(path.exists())

    def test_uncooperative_replacement_during_release_is_not_unlinked(self):
        marker = self.arm()
        original = lifecycle.task_boundary_locked
        changed = dict(marker, collaboration_id='replacement')

        def replace_during_release(value, event, **kwargs):
            result = original(value, event, **kwargs)
            self.path(marker).write_text(json.dumps(changed))
            return result

        with patch.object(lifecycle, 'task_boundary_locked', side_effect=replace_during_release):
            with self.assertRaisesRegex(RuntimeError, 'changed during release'):
                harness.disarm_task('child')
        self.assertEqual(json.loads(self.path(marker).read_text()), changed)

    def test_heartbeat_read_before_disarm_cannot_recreate_marker(self):
        original = harness._marker_update_lock
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                path = self.legacy() if legacy else self.path(self.arm())
                entered, finish = Event(), Event()

                @contextmanager
                def pause_before_lock(path, observed, **kwargs):
                    if current_thread() is not main_thread():
                        entered.set()
                        if not finish.wait(3):
                            raise AssertionError('test did not release heartbeat')
                    with original(path, observed, **kwargs):
                        yield

                with patch.object(harness, '_marker_update_lock', pause_before_lock), \
                        ThreadPoolExecutor(max_workers=1) as workers:
                    heartbeat = workers.submit(harness._touch_active_marker, self.root / 'receipt.json')
                    try:
                        self.assertTrue(entered.wait(2))
                        self.assertEqual(harness.disarm_task(), 1)
                        self.assertFalse(path.exists())
                    finally:
                        finish.set()
                    heartbeat.result(timeout=2)
                self.assertFalse(path.exists())
                self.assertEqual(list(self.active.rglob('*.hb.*')), [])
                self.assert_paused(CALLER)

    def test_inflight_heartbeat_excludes_disarm_with_and_without_caller_uuid(self):
        original = harness.os.replace
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                path = self.legacy() if legacy else self.path(self.arm())
                entered, finish = Event(), Event()

                def pause_replace(source, target):
                    if '.hb.' in Path(source).name:
                        entered.set()
                        if not finish.wait(3):
                            raise AssertionError('test did not release replacement')
                    return original(source, target)

                with patch.object(harness.os, 'replace', side_effect=pause_replace), \
                        ThreadPoolExecutor(max_workers=2) as workers:
                    heartbeat = workers.submit(harness._touch_active_marker, self.root / 'receipt.json')
                    try:
                        self.assertTrue(entered.wait(2))
                        disarmed = workers.submit(harness.disarm_task)
                        with self.assertRaisesRegex(RuntimeError, 'busy'):
                            disarmed.result(timeout=1)
                        self.assertTrue(path.exists())
                        before = path.read_bytes()
                        receipt = self.root / 'busy-marker.json'
                        workers.submit(harness._write, receipt, {'complete': True}).result(timeout=1)
                        self.assertEqual(json.loads(receipt.read_text()), {'complete': True})
                        self.assertEqual(path.read_bytes(), before)
                    finally:
                        finish.set()
                    heartbeat.result(timeout=2)
                self.assertEqual(harness.disarm_task(), 1)
                harness._touch_active_marker(self.root / 'receipt.json')
                self.assertFalse(path.exists())

    def test_busy_heartbeat_skips_only_busy_marker_and_preserves_normal_write(self):
        busy = self.arm()
        healthy = self.arm('healthy', callers=(OTHER,))
        before = self.path(busy).read_bytes()
        with reask.episode_lock(CALLER), ThreadPoolExecutor(max_workers=1) as workers:
            receipt = self.root / 'receipt.json'
            workers.submit(harness._write, receipt, {'complete': True}).result(timeout=1)
        self.assertEqual(json.loads(receipt.read_text()), {'complete': True})
        self.assertEqual(self.path(busy).read_bytes(), before)
        after = json.loads(self.path(healthy).read_text())
        self.assertNotEqual(after['last_activity_at'], healthy['last_activity_at'])
        self.assertTrue(harness._same_active_marker(after, healthy))

    def test_heartbeat_refuses_changed_identity_and_cleans_failed_private_temp(self):
        marker = self.arm()
        path = self.path(marker)
        changed = dict(marker, task_id='replacement')
        original_lock = harness._marker_update_lock

        @contextmanager
        def replace_before_lock(path, observed, **kwargs):
            path.write_text(json.dumps(changed))
            with original_lock(path, observed, **kwargs):
                yield

        with patch.object(harness, '_marker_update_lock', replace_before_lock):
            harness._touch_active_marker(self.root / 'receipt.json')
        self.assertEqual(json.loads(path.read_text()), changed)
        before = path.read_bytes()
        original_replace = harness.os.replace

        def fail_heartbeat(source, target):
            if '.hb.' in Path(source).name:
                raise OSError('heartbeat write failed')
            return original_replace(source, target)

        with patch.object(harness.os, 'replace', side_effect=fail_heartbeat):
            harness._write(self.root / 'receipt.json', {'complete': True})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob('.*.hb.*')), [])
        self.assertEqual(json.loads((self.root / 'receipt.json').read_text()), {'complete': True})


if __name__ == '__main__':
    unittest.main()
