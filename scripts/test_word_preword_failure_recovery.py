"""Synthetic evidence/SQLite tests. No live paths, network, Word or Zotero."""
from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import py_compile
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import word_preword_failure_recovery as m


class Crash(BaseException):
    pass


class Fixture:
    def __init__(self, base):
        self.base = base
        self.root = base / 'queue'; self.root.mkdir()
        self.boot = base / 'dispatches/bootstrap'; self.boot.mkdir(parents=True)
        self.close = base / 'close-once'; self.close.mkdir()
        self.reconcile = base / 'reconcile-once'; self.reconcile.mkdir()
        self.obs = self.reconcile / 'observation/six-zero'; self.obs.mkdir(parents=True)
        self.live = base / 'word-automation.pending.json'
        self.out = base / 'settlement'
        self.files = {}
        def j(label, path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False) + '\n')
            self.files[label] = m.pin(path)
            return self.files[label]
        self.j = j
        actor = dict(sid='original-execution-sid', uid=os.getuid(), pid=123,
                     birth='test-birth', executable='/mock/codex', argv=['codex', 'resume', 'original-execution-sid'])
        self.actor = actor
        self.runner = base / 'bootstrap.py'; self.runner.write_text('raise RuntimeError("TEST_ESCAPED_ORIGINAL_GROUP")\n')
        self.helper = base / 'helper.applescript'
        self.helper.write_text(' ' * 11354 + 'count of (every window of p whose name is "Citation Dialog")')
        verifier = base / 'verifier.py'; verifier.write_text('# static synthetic verifier\n')
        self.files.update(helper=m.pin(self.helper), verifier=m.pin(verifier))
        data, profile = base / 'data', base / 'profile'
        data.mkdir(); profile.mkdir()
        cfg = dict(data=str(data), profile=str(profile), production=dict(pid=33733))
        self.cfg = cfg
        plan = dict(ticket_id=m.TICKET, task_id=m.TASK, actor=actor, execution_actor=actor,
                    original_controller=dict(sid='original-controller'), runner=m.pin(self.runner))
        j('plan', base / 'PLAN.json', plan); j('config', base / 'INPUT.json', cfg)
        j('controller', base / 'controller.json', dict(status='ACTIVE', actor='original-controller', task_id=m.TASK))
        j('control', base / 'control.json', dict(status='ACTIVE', actor=actor['sid'], task_id=m.TASK))
        j('authorization', base / 'authorization.json', dict(sid=actor['sid']))
        row = dict(id=m.TICKET, task_id=m.TASK, workspace_uuid='workspace', surface_uuid='surface',
                   resource='word-zotero', status='ACTIVE', requested=1.0, token='fixture-token',
                   expires=2.0, pgid=None, release_proof=None, invocations=1)
        self.row = row
        j('return', self.boot / 'RETURN.json', dict(ticket=m.TICKET, task_id=m.TASK, lease_after=row,
             result=dict(exit_code=1, pgid=53851, process_group_empty=True, stdout='',
                         stderr='line 403, in launch_cycle\nRuntimeError: TEST_ESCAPED_ORIGINAL_GROUP')))
        j('failure', self.boot / 'WORKER-FAILURE.json', dict(error='TEST_ESCAPED_ORIGINAL_GROUP', entered=True, no_retry=True))
        j('spawn', self.boot / '01-initialize/SPAWN.json', dict(launcher_pid=54918, pgid=53851))
        argv = ['/Applications/Zotero.app/Contents/MacOS/zotero', '-no-remote', '-profile',
                cfg['profile'], '-datadir', cfg['data'], '-ZoteroDebugText']
        j('start', self.boot / '01-initialize/START-INTENT.json', dict(argv=argv, no_retry=True))
        initial = j('initial_pending', self.reconcile / 'ORIGINAL-PENDING.json', dict(ticket=m.TICKET, task_id=m.TASK))
        initial_inode = m.identity(initial['path'])
        j('archived', self.reconcile / 'PENDING-ARCHIVED.json', dict(archive=initial,
            original=dict(initial, path=str(self.live)), inode=initial_inode[1], failure_preserved=True, business_success=False))
        j('closed', self.close / 'CLOSED.json', dict(status='EXACT_FAILED_TEST_NORMALLY_CLOSED_PENDING_RETAINED',
            target_pid=55836, target_birth='Fri Nov 20 19:42:32 2026', test_port_empty=True,
            test_database_handles_empty=True, original_business_success=False, pending=initial))
        out = self.close / 'stdout.txt'; out.write_text('normal close\n')
        err = self.close / 'stderr.txt'; err.write_text('')
        j('quit', self.close / 'QUIT-RETURN.json', dict(returncode=0, stdout=m.pin(out), stderr=m.pin(err)))
        j('quit_intent', self.close / 'QUIT-INTENT.json', dict(target=dict(pid=55836,
            birth='Fri Nov 20 19:42:32 2026', command=' '.join(argv))))
        host = dict(pid=83331, pgid=83331, at='original-host-time')
        j('observer_host', self.obs / 'HOST.json', host)
        j('observer_result', self.obs / 'RESULT.json', dict(host=host, returncode=1, stdout='', group_empty=True,
            stderr=str(self.helper) + ':11354:11414: execution error: System Events發生錯誤：osascript不允許輔助取用。 (-25211)\n'))
        j('observer_intent', self.obs / 'INTENT.json', dict(ticket=m.TICKET, task_id=m.TASK,
            argv=['/usr/bin/osascript', str(self.helper), '33733', str(self.obs / 'ZOTERO-READONLY.js'), 'async', '30'], no_retry=True))
        j('stop', self.reconcile / 'STOP.json', dict(error='OBSERVER_UNCONFIRMED:ZOTERO-RUNJS',
            no_retry=True, original_pending_archived=True, business_success=False))
        j('observer_failed', self.obs / 'FAILED.json', dict(error='OBSERVER_UNCONFIRMED:ZOTERO-RUNJS', no_retry=True))
        pending_value = dict(ticket=m.TICKET, task_id=m.TASK, owner=m.TASK, pid=81821,
            stage='six-zero-zotero-observation', attempt=str(self.obs), status='UNKNOWN_NATIVE_RESULT',
            error='OBSERVER_UNCONFIRMED:ZOTERO-RUNJS', host=[], no_retry=True)
        self.live.write_text(json.dumps(pending_value))
        pending = m.pin(self.live)
        successor = base / 'successor.py'; successor.write_text('# original successor\n')
        sources = j('sources', base / 'SOURCES.json', {})
        reviewed = j('reviewed', base / 'REVIEWED.json', dict(files=[m.pin(successor)], original_sources=sources))
        old_actor = j('old_actor', base / 'OLD-ACTOR.json', actor)
        old_seal = j('old_seal', base / 'OLD-SEAL.json', dict(original=True))
        j('history', base / 'HISTORY.json', dict(ticket=m.TICKET, pending=pending,
            initial_pending_inode=initial_inode[1], original_attempt=str(self.reconcile),
            settlement_context=m.pin(successor), reconciliation=m.pin(successor), reviewed_code=reviewed,
            original_sources=sources, old_actor=old_actor, old_seal=old_seal,
            close_receipt=self.files['closed'], original_files={}))
        queue_lock = self.root / (hashlib.sha256(b'word-zotero').hexdigest() + '.lock')
        lock_paths = [queue_lock, base / 'word-automation.lock', base / 'desktop-focus.lock']
        for path in lock_paths:
            path.touch()
        self.p = dict(profile=m.PROFILE, ticket=m.TICKET, task=m.TASK, root=str(self.root),
            settlement=str(self.out), pending=pending, pending_identity=m.identity(self.live),
            initial_pending_identity=initial_inode, files=self.files, extra_files=[m.pin(self.runner)],
            trees={str(p): m.tree(p) for p in [self.boot.parent, self.close, self.reconcile]},
            locks={str(p): m.identity(p) for p in lock_paths}, expected_lease=row,
            absent_paths=[str(self.boot / 'plugin'), str(self.boot / '02-refresh'), str(base / 'ZoteroPort.txt')])
        with closing(sqlite3.connect(self.root / 'queue.sqlite3')) as db, db:
            db.execute('CREATE TABLE tickets(id TEXT PRIMARY KEY,task_id TEXT,workspace_uuid TEXT,surface_uuid TEXT,'
                'resource TEXT,status TEXT,requested REAL,token TEXT,expires REAL,pgid INTEGER,release_proof TEXT,invocations INTEGER)')
            db.execute('CREATE TABLE resources(name TEXT PRIMARY KEY,external_locks TEXT,release_requirements TEXT)')
            db.execute('INSERT INTO tickets VALUES (' + ','.join('?' for _ in row) + ')', list(row.values()))
            db.execute('INSERT INTO resources VALUES (?,?,?)', ('word-zotero',
                json.dumps([str(base / 'word-automation.lock')]), json.dumps(m.SIX_ZERO)))

    def change(self, label, mutate, repin=False):
        path = Path(self.files[label]['path']); value = json.loads(path.read_bytes())
        mutate(value); path.write_text(json.dumps(value, ensure_ascii=False))
        if repin:
            self.files[label] = m.pin(path)
            self.p['trees'] = {k: m.tree(k) for k in self.p['trees']}

    def dbrow(self):
        with m.connection(self.p) as db:
            return m.rows(db, self.p)[0]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(Path(self.tmp.name).resolve())
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(m, '_profile', return_value=self.f.p))
        self.actor = self.stack.enter_context(patch.object(m, '_actor', return_value=self.f.actor))
        self.os_state = self.stack.enter_context(patch.object(m, '_os_state', return_value=dict(
            owned_pids_absent=True, owned_groups_empty=True, owned_handles_empty=True,
            owned_port_empty=True, owned_runners_absent=True)))
        # Any accidental native process, network entry or signal in this suite fails.
        self.stack.enter_context(patch.object(m.subprocess, 'run', side_effect=AssertionError('NO_NATIVE_PROCESS')))
        self.stack.enter_context(patch.object(m.os, 'kill', side_effect=AssertionError('NO_SIGNAL')))
        self.stack.enter_context(patch.object(m.os, 'killpg', side_effect=AssertionError('NO_SIGNAL')))
        self.tool = m.Recovery()

    def preview(self):
        return self.tool.preview()['plan_sha256']

    def assert_retained(self):
        self.assertEqual(self.f.dbrow(), self.f.row)
        self.assertTrue(self.f.live.exists())

    def test_preview_is_readonly_and_global_state_unassessed(self):
        before = self.f.dbrow()
        result = self.tool.preview()
        self.assertEqual(result['global_word_state'], 'NOT_ASSESSED')
        self.assertEqual(before, self.f.dbrow())
        self.assertFalse(self.f.out.exists())

    def test_release_with_foreign_unsaved_document_preserved(self):
        foreign = self.f.base / 'user-unsaved.docx'; foreign.write_bytes(b'unsaved foreign document')
        result = self.tool.apply(self.preview())
        self.assertEqual(result['outcome'], 'FAILED_PREWORD_RESOURCES_RELEASED')
        row = self.f.dbrow()
        self.assertEqual(row['status'], 'RELEASED'); self.assertEqual(row['invocations'], 1)
        self.assertEqual({k: v for k, v in row.items() if k not in ('status', 'release_proof')},
                         {k: v for k, v in self.f.row.items() if k not in ('status', 'release_proof')})
        self.assertFalse(self.f.live.exists())
        archive = self.f.out / 'PENDING-ARCHIVE.json'
        self.assertEqual(m.identity(archive), self.f.p['pending_identity'])
        self.assertEqual(foreign.read_bytes(), b'unsaved foreign document')
        self.assertNotIn('documents', result['release_proof'])
        self.assertFalse(result['business_success'])
        self.assertEqual(self.tool.inspect()['queue_changes'], 0)

    def test_wrong_ticket_and_profile_refused(self):
        for kw in [dict(ticket='foreign-ticket'), dict(profile='GENERIC_UNKNOWN')]:
            with self.subTest(kw=kw), self.assertRaises(m.Refused):
                self.tool.preview(**kw)
        self.assert_retained()

    def test_unknown_or_successful_business_rejected_even_with_new_pins(self):
        self.f.change('return', lambda v: v['result'].update(exit_code=0), repin=True)
        with self.assertRaisesRegex(m.Refused, 'BUSINESS_TERMINAL'):
            self.preview()
        self.assert_retained()

    def test_invocation_changed(self):
        with closing(sqlite3.connect(self.f.root / 'queue.sqlite3')) as db, db:
            db.execute('UPDATE tickets SET invocations=2')
        with self.assertRaisesRegex(m.Refused, 'EXACT_ACTIVE'):
            self.preview()
        self.assertTrue(self.f.live.exists())

    def test_code_pin_changed(self):
        self.f.runner.write_text('run_word()')
        with self.assertRaisesRegex(m.Refused, 'PIN_CHANGED'):
            self.preview()
        self.assert_retained()

    def test_new_uncovered_log_refused(self):
        (self.f.boot / 'LATER-INTENT.json').write_text('{}')
        with self.assertRaisesRegex(m.Refused, 'EVIDENCE_SET'):
            self.preview()
        self.assert_retained()

    def test_plugin_call_directory_refused(self):
        (self.f.boot / 'plugin').mkdir()
        with self.assertRaises(m.Refused):
            self.preview()
        self.assert_retained()

    def test_wrong_ax_offset_despite_same_error_number(self):
        self.f.change('observer_result', lambda v: v.update(stderr=v['stderr'].replace('11354:11414', '20000:20010')), repin=True)
        with self.assertRaisesRegex(m.Refused, 'PREINPUT_AX'):
            self.preview()
        self.assert_retained()

    def test_observer_stdout_is_input_uncertainty(self):
        self.f.change('observer_result', lambda v: v.update(stdout='RUNJS_SUBMITTED'), repin=True)
        with self.assertRaisesRegex(m.Refused, 'OBSERVER_NOT_TERMINAL'):
            self.preview()
        self.assert_retained()

    def test_wrong_close_birth_refused(self):
        self.f.change('closed', lambda v: v.update(target_birth='different birth'), repin=True)
        with self.assertRaisesRegex(m.Refused, 'NORMAL_CLOSE'):
            self.preview()
        self.assert_retained()

    def test_original_pending_archive_broken(self):
        self.f.change('archived', lambda v: v.update(inode=100), repin=True)
        with self.assertRaisesRegex(m.Refused, 'INITIAL_PENDING'):
            self.preview()
        self.assert_retained()

    def test_current_pending_replaced_same_content(self):
        raw = self.f.live.read_bytes(); self.f.live.rename(self.f.base / 'old-pending')
        self.f.live.write_bytes(raw)
        with self.assertRaisesRegex(m.Refused, 'PENDING_COMPARE'):
            self.preview()
        self.assert_retained()

    def test_current_pending_changed(self):
        self.f.live.write_text('{"owner":"other"}')
        with self.assertRaisesRegex(m.Refused, 'PENDING_COMPARE'):
            self.preview()
        self.assert_retained()

    def test_symlink_pending_refused(self):
        other = self.f.base / 'actual'; self.f.live.rename(other); self.f.live.symlink_to(other)
        with self.assertRaisesRegex(m.Refused, 'REGULAR_FILE'):
            self.preview()
        self.assertTrue(other.exists()); self.assertEqual(self.f.dbrow(), self.f.row)

    def test_actor_or_os_unknown_refused(self):
        self.actor.side_effect = m.Refused('ACTUAL_ROOT_THREAD_REQUIRED')
        with self.assertRaisesRegex(m.Refused, 'ACTUAL_ROOT'):
            self.preview()
        self.actor.side_effect = None
        self.os_state.side_effect = m.Refused('OWNED_GROUP_PRESENT_OR_UNKNOWN')
        with self.assertRaisesRegex(m.Refused, 'OWNED_GROUP'):
            self.preview()
        self.assert_retained()

    def test_resource_configuration_changed(self):
        with closing(sqlite3.connect(self.f.root / 'queue.sqlite3')) as db, db:
            db.execute("UPDATE resources SET release_requirements='{}'")
        with self.assertRaisesRegex(m.Refused, 'RESOURCE_CONFIGURATION'):
            self.preview()
        self.assert_retained()

    def test_wrong_plan_hash(self):
        with self.assertRaisesRegex(m.Refused, 'EXACT_PLAN'):
            self.tool.apply('0' * 64)
        self.assert_retained(); self.assertFalse(self.f.out.exists())

    def test_lock_busy_refused_without_writes(self):
        import fcntl
        path = next(k for k in self.f.p['locks'] if k.endswith('word-automation.lock'))
        with open(path, 'r+b') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.tool.apply(self.preview())
        self.assert_retained(); self.assertFalse(self.f.out.exists())

    def test_preview_to_apply_race(self):
        sha = self.preview()
        with closing(sqlite3.connect(self.f.root / 'queue.sqlite3')) as db, db:
            db.execute('UPDATE tickets SET token=?', ('new-token',))
        with self.assertRaises(m.Refused):
            self.tool.apply(sha)
        self.assertTrue(self.f.live.exists()); self.assertFalse(self.f.out.exists())

    def test_duplicate_apply_has_no_new_writes(self):
        sha = self.preview(); self.tool.apply(sha)
        row = self.f.dbrow()
        self.assertEqual(self.tool.apply(sha)['outcome'], 'FAILED_PREWORD_RESOURCES_RELEASED')
        self.assertEqual(self.f.dbrow(), row)

    def crash_at(self, stage):
        def fail(current):
            if current == stage:
                raise Crash(stage)
        with patch.object(m, '_checkpoint', side_effect=fail), self.assertRaises(Crash):
            self.tool.apply(self.preview())
        return m.pin(self.f.out / 'INTENT.json')['sha256']

    def test_interruption_after_intent_preserves_active_and_pending(self):
        sha = self.crash_at('intent'); self.assert_retained()
        self.tool.recover(sha)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED'); self.assertFalse(self.f.live.exists())

    def test_interruption_after_link_recovers_same_inode(self):
        sha = self.crash_at('archive_link'); self.assert_retained()
        self.assertEqual(self.f.live.stat().st_nlink, 2)
        self.tool.recover(sha)
        self.assertEqual((self.f.out / 'PENDING-ARCHIVE.json').stat().st_nlink, 1)

    def test_interruption_before_commit_rolls_back_and_keeps_pending(self):
        sha = self.crash_at('before_commit'); self.assert_retained()
        self.tool.recover(sha)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')

    def test_interruption_after_commit_keeps_pending_until_recovery(self):
        sha = self.crash_at('committed_db')
        row = self.f.dbrow(); self.assertEqual(row['status'], 'ACTIVE')
        self.assertIsNotNone(row['release_proof'])
        self.assertTrue(self.f.live.exists()); self.assertFalse((self.f.out / 'COMPLETE.json').exists())
        self.assertEqual(self.tool.inspect()['queue_changes'], 0)
        self.assertTrue(self.f.live.exists())
        from resource_broker import Broker, ResourceBusy
        broker = Broker(self.f.root)
        try:
            next_ticket = broker.request('word-zotero', 'next-task',
                '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')
            with self.assertRaises(ResourceBusy):
                broker.grant(next_ticket['id'])
        finally:
            broker.db.close()
        self.tool.recover(sha)
        self.assertEqual(self.f.dbrow(), dict(row, status='RELEASED')); self.assertFalse(self.f.live.exists())

    def test_interruption_after_unlink_recovers_only_receipt(self):
        sha = self.crash_at('unlinked'); row = self.f.dbrow()
        self.assertEqual(row['status'], 'ACTIVE')
        self.assertFalse(self.f.live.exists())
        self.tool.recover(sha)
        self.assertEqual(self.f.dbrow(), dict(row, status='RELEASED')); self.assertTrue((self.f.out / 'COMPLETE.json').exists())

    def test_recovery_wrong_intent_refused(self):
        self.crash_at('intent')
        with self.assertRaisesRegex(m.Refused, 'EXACT_ORIGINAL'):
            self.tool.recover('0' * 64)
        self.assert_retained()

    def test_recovery_refuses_same_json_with_changed_bytes_between_lock_checks(self):
        sha = self.crash_at('intent')
        real_locks = m.locks
        @contextmanager
        def raced_locks(p):
            path = self.f.out / 'INTENT.json'
            path.write_bytes(path.read_bytes() + b'\n')
            with real_locks(p):
                yield
        with patch.object(m, 'locks', raced_locks), self.assertRaisesRegex(m.Refused, 'INTENT_CHANGED'):
            self.tool.recover(sha)
        self.assert_retained()

    def test_recovery_preserves_foreign_pending_after_commit(self):
        sha = self.crash_at('committed_db')
        self.f.live.unlink(); self.f.live.write_bytes(b'foreign pending')
        with self.assertRaises(m.Refused):
            self.tool.recover(sha)
        self.assertEqual(self.f.live.read_bytes(), b'foreign pending')
        self.assertEqual(self.f.dbrow()['status'], 'ACTIVE')

    def test_recovery_refuses_missing_live_pending_before_commit(self):
        sha = self.crash_at('archived'); self.f.live.unlink()
        with self.assertRaisesRegex(m.Refused, 'EVENTS_AHEAD_OF_DATABASE_COMMIT'):
            self.tool.recover(sha)
        self.assertEqual(self.f.dbrow(), self.f.row)

    def test_recovery_refuses_unknown_event(self):
        sha = self.crash_at('intent'); (self.f.out / 'NEW-INPUT.json').write_text('{}')
        with self.assertRaisesRegex(m.Refused, 'FILE_SET'):
            self.tool.recover(sha)
        self.assert_retained()

    def test_queue_trigger_refused(self):
        with closing(sqlite3.connect(self.f.root / 'queue.sqlite3')) as db, db:
            db.execute('CREATE TRIGGER unwanted AFTER UPDATE ON tickets BEGIN UPDATE resources SET release_requirements="{}"; END')
        with self.assertRaisesRegex(m.Refused, 'QUEUE_TRIGGER'):
            self.preview()
        self.assertTrue(self.f.live.exists())

    def test_empty_preintent_directory_can_continue_without_replaying_business(self):
        def fail(stage):
            if stage == 'before_intent':
                raise Crash(stage)
        sha = self.preview()
        with patch.object(m, '_checkpoint', side_effect=fail), self.assertRaises(Crash):
            self.tool.apply(sha)
        self.assertEqual(list(self.f.out.iterdir()), [])
        self.assert_retained()
        self.tool.apply(sha)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')

    def test_partial_unpublished_intent_preserved_and_safe_to_publish_again(self):
        def fail(stage):
            if stage == 'writing:INTENT.json':
                raise Crash(stage)
        sha = self.preview()
        with patch.object(m, '_checkpoint', side_effect=fail), self.assertRaises(Crash):
            self.tool.apply(sha)
        self.assertFalse((self.f.out / 'INTENT.json').exists())
        partial = next(self.f.out.iterdir()); raw = partial.read_bytes()
        self.assert_retained()
        self.tool.apply(sha)
        self.assertEqual(partial.read_bytes(), raw)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')

    def test_partial_unpublished_committed_receipt_recovered_without_losing_bytes(self):
        sha = self.crash_at('writing:COMMITTED.json')
        self.assertEqual(self.f.dbrow()['status'], 'ACTIVE'); self.assertTrue(self.f.live.exists())
        partial = next(self.f.out.glob('.COMMITTED.json.unpublished-*')); raw = partial.read_bytes()
        self.tool.recover(sha)
        self.assertEqual(partial.read_bytes(), raw)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')

    def test_published_temp_link_can_be_preserved_during_recovery(self):
        sha = self.crash_at('published:ARCHIVED.json')
        self.assert_retained()
        self.tool.recover(sha)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')

    def test_forged_late_event_refused_before_any_new_mutation(self):
        sha = self.crash_at('intent')
        (self.f.out / 'COMPLETE.json').write_text('{"fake":true}')
        with self.assertRaisesRegex(m.Refused, 'EVENTS_BEFORE_ARCHIVE'):
            self.tool.recover(sha)
        self.assert_retained(); self.assertFalse((self.f.out / 'PENDING-ARCHIVE.json').exists())

    def test_damaged_cleanup_event_refused_before_pending_unlink(self):
        sha = self.crash_at('cleanup_intent')
        (self.f.out / 'CLEANUP-INTENT.json').write_text('{"fake":true}')
        before = self.f.dbrow()
        with self.assertRaisesRegex(m.Refused, 'SETTLEMENT_EVENT_CHANGED'):
            self.tool.recover(sha)
        self.assertTrue(self.f.live.exists()); self.assertEqual(self.f.dbrow(), before)

    def test_missing_pending_requires_durable_cleanup_intent(self):
        sha = self.crash_at('committed_db')
        self.f.live.unlink()
        with self.assertRaisesRegex(m.Refused, 'MISSING_PENDING_WITHOUT_CLEANUP_INTENT'):
            self.tool.recover(sha)
        self.assertEqual(self.f.dbrow()['status'], 'ACTIVE')

    def test_interruption_after_final_release_only_completes_receipt(self):
        sha = self.crash_at('released_db'); before = self.f.dbrow()
        self.assertEqual(before['status'], 'RELEASED'); self.assertFalse(self.f.live.exists())
        self.tool.recover(sha)
        self.assertEqual(self.f.dbrow(), before)

    def test_interruption_before_final_release_keeps_queue_active_after_unlink(self):
        sha = self.crash_at('before_release_commit')
        self.assertEqual(self.f.dbrow()['status'], 'ACTIVE'); self.assertFalse(self.f.live.exists())
        self.tool.recover(sha)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')

    def test_actual_successor_source_reference_checked(self):
        (self.f.base / 'successor.py').write_text('unexpected_input()')
        with self.assertRaisesRegex(m.Refused, 'PIN_CHANGED'):
            self.preview()
        self.assert_retained()

    def test_intent_changed_during_apply_stops_before_next_mutation(self):
        for stage in ['intent', 'committed_db', 'cleanup_intent']:
            with self.subTest(stage=stage):
                # Each independent state machine has its own temporary queue.
                with tempfile.TemporaryDirectory() as temp:
                    f = Fixture(Path(temp).resolve())
                    with patch.object(m, '_profile', return_value=f.p), patch.object(m, '_actor', return_value=f.actor):
                        tool = m.Recovery(); sha = tool.preview()['plan_sha256']
                        def corrupt(current):
                            if current == stage:
                                (f.out / 'INTENT.json').write_text('{"changed":true}')
                        with patch.object(m, '_checkpoint', side_effect=corrupt), self.assertRaises(m.Refused):
                            tool.apply(sha)
                        self.assertTrue(f.live.exists())
                        self.assertEqual(f.dbrow()['status'], 'ACTIVE')
                        if stage == 'intent':
                            self.assertEqual(f.dbrow(), f.row)

    def test_archive_receipt_changed_before_commit_keeps_original_ticket(self):
        def corrupt(stage):
            if stage == 'archived':
                (self.f.out / 'ARCHIVED.json').write_text('{"changed":true}')
        with patch.object(m, '_checkpoint', side_effect=corrupt), self.assertRaises(m.Refused):
            self.tool.apply(self.preview())
        self.assert_retained()

    def test_unlink_before_directory_fsync_recovers_with_sync(self):
        sha = self.crash_at('unlinked_unsynced')
        self.assertEqual(self.f.dbrow()['status'], 'ACTIVE'); self.assertFalse(self.f.live.exists())
        real_sync = m.sync_dir
        with patch.object(m, 'sync_dir', wraps=real_sync) as sync:
            self.tool.recover(sha)
        self.assertIn(((self.f.live.parent,), {}), sync.call_args_list)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')

    def test_receipt_link_before_directory_fsync_recovers_with_sync(self):
        sha = self.crash_at('linked_unsynced:ARCHIVED.json')
        real_sync = m.sync_dir
        with patch.object(m, 'sync_dir', wraps=real_sync) as sync:
            self.tool.recover(sha)
        self.assertIn(((self.f.out,), {}), sync.call_args_list)
        self.assertEqual(self.f.dbrow()['status'], 'RELEASED')


class OSObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(Path(self.tmp.name).resolve())
        self.values = m.validate_history(self.f.p)

    def test_foreign_word_does_not_affect_owned_empty_observation(self):
        with patch.object(m.os, 'kill', side_effect=ProcessLookupError), \
             patch.object(m.os, 'killpg', side_effect=ProcessLookupError), \
             patch.object(m, '_run', side_effect=lambda a, **k: '12 /Applications/Microsoft Word.app\n' if a[0] == '/bin/ps' else ''):
            self.assertTrue(all(m._os_state(self.f.p, self.values).values()))

    def test_live_owned_pid_refused(self):
        with patch.object(m.os, 'kill', return_value=None), self.assertRaisesRegex(m.Refused, 'OWNED_PID'):
            m._os_state(self.f.p, self.values)

    def test_permission_error_is_not_absence(self):
        with patch.object(m.os, 'kill', side_effect=PermissionError), self.assertRaises(PermissionError):
            m._os_state(self.f.p, self.values)

    def test_live_owned_runner_refused(self):
        with patch.object(m.os, 'kill', side_effect=ProcessLookupError), \
             patch.object(m.os, 'killpg', side_effect=ProcessLookupError), \
             patch.object(m, '_run', return_value='55 ' + str(self.f.runner)), \
             self.assertRaisesRegex(m.Refused, 'OWNED_RUNNER'):
            m._os_state(self.f.p, self.values)

    def test_owned_file_handle_refused(self):
        with patch.object(m.os, 'kill', side_effect=ProcessLookupError), \
             patch.object(m.os, 'killpg', side_effect=ProcessLookupError), \
             patch.object(m, '_run', side_effect=lambda a, **k: '1 launchd\n' if a[0] == '/bin/ps' else '55\n'), \
             self.assertRaisesRegex(m.Refused, 'OWNED_FILE_HANDLE'):
            m._os_state(self.f.p, self.values)

    def test_empty_or_malformed_process_inventory_refused(self):
        for inventory in ['', 'not-a-pid command', '12', '12 cmd\n12 other']:
            with self.subTest(inventory=inventory), \
                 patch.object(m.os, 'kill', side_effect=ProcessLookupError), \
                 patch.object(m.os, 'killpg', side_effect=ProcessLookupError), \
                 patch.object(m, '_run', return_value=inventory), \
                 self.assertRaisesRegex(m.Refused, 'PROCESS_INVENTORY'):
                m._os_state(self.f.p, self.values)


class ActorSourceTests(unittest.TestCase):
    def test_verified_source_executes_even_with_matching_poisoned_pyc(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve(); source = base / 'actor.py'; marker = base / 'poison-ran'
            actor = dict(sid='same', pid=100, uid=os.getuid(), birth='same', executable='codex', argv=['codex'])
            good = ('def discover(authorization, old):\n    return {"actor": old}\n'
                    'def check(actor, authorization):\n    return True\n')
            poison = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
            size = max(len(good), len(poison)) + 10
            good = good.ljust(size, ' '); poison = poison.ljust(size, ' ')
            source.write_text(poison); before = source.stat()
            py_compile.compile(str(source), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
            source.write_text(good); os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            p = dict(files=dict(verifier=m.pin(source), authorization=dict(path=str(base / 'auth'))))
            result = m._actor(p, dict(plan=dict(actor=actor)))
            self.assertEqual(result, actor); self.assertFalse(marker.exists())


class OrdinaryReleaseRegressionTests(unittest.TestCase):
    def setUp(self):
        from resource_broker import Broker
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.broker = Broker(self.base); self.addCleanup(self.broker.db.close)
        self.broker.configure('word-zotero')
        self.ticket = self.broker.request('word-zotero', 'synthetic-ordinary',
            '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')
        self.ticket = self.broker.grant(self.ticket['id'])

    def test_ordinary_release_still_requires_global_six_zero(self):
        from resource_broker import ResourceBusy
        receipt = self.base / 'scoped.json'
        receipt.write_text(json.dumps(dict(task_id='synthetic-ordinary', global_word_state='NOT_ASSESSED')))
        proof = dict(pending=False, own_processes_empty=True, observed_receipt=m.pin(receipt))
        with self.assertRaisesRegex(ResourceBusy, 'NATIVE_RELEASE_CONDITION_FAILED'):
            self.broker.release(self.ticket['id'], self.ticket['token'], proof, reconcile=True)
        self.assertEqual(self.broker.get(self.ticket['id'])['status'], 'ACTIVE')

    def test_ordinary_unknown_remains_blocked(self):
        from resource_broker import ResourceBusy
        with self.assertRaisesRegex(ResourceBusy, 'DRAIN_REQUIRED'):
            self.broker.release(self.ticket['id'], self.ticket['token'],
                                dict(pending=True, own_processes_empty=True), reconcile=True)
        self.assertEqual(self.broker.get(self.ticket['id'])['status'], 'ACTIVE')

    def test_ordinary_six_zero_release_still_works(self):
        receipt = self.base / 'six-zero.json'
        receipt.write_text(json.dumps(dict(task_id='synthetic-ordinary', **m.SIX_ZERO)))
        proof = dict(pending=False, own_processes_empty=True, observed_receipt=m.pin(receipt))
        result = self.broker.release(self.ticket['id'], self.ticket['token'], proof, reconcile=True)
        self.assertEqual(result['status'], 'RELEASED')


if __name__ == '__main__':
    unittest.main()
