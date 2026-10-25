"""Original pending-job closeout controls; no real application or queue writes."""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from resource_broker import ResourceBusy, os_lock
import word_restricted_broker as protocol
import word_preinput_recovery as recovery
import test_word_restricted_revision as owned_tests


class PreinputRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = owned_tests.OwnedRevisionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.ticket, self.plan, self.plan_ref, _contract = self.f.request()
        self.broker = self.f.f.broker
        granted = self.broker.grant(self.ticket['id'])
        nonce, pgid = str(uuid.uuid4()), 100000800
        argv = ['/private/python', '-BE', str(Path(self.plan['controller_source']) / 'scripts/word_restricted_revision.py'),
                '--root', str(self.broker.root), '--ticket', self.ticket['id'],
                '--token', granted['token'], '--nonce', nonce]
        error = dict(status='RESTRICTED_NATIVE_PENDING', reason=recovery.PREINPUT_REASON,
                     error_type='ResourceBusy', no_retry=True, no_foreign_save_or_close=True, legacy_released=False)
        self.original_result = dict(exit_code=2, stdout='', stderr=json.dumps(error), pgid=pgid,
                                    process_group_empty=True, original_argv=argv)
        self.broker.db.execute("UPDATE word_restricted_tickets SET status='PENDING',invocations=1,nonce=?,parent_pid=100000700,pgid=?,result=? WHERE id=?",
                               (nonce, pgid, json.dumps(self.original_result), self.ticket['id']))
        root = Path(self.plan['evidence_root']); root.mkdir()
        self.failure = root / 'failure.json'
        self.failure.write_text(json.dumps(dict(reason=recovery.PREINPUT_REASON, error_type='ResourceBusy',
            may_be_open=False, owned_document='None', retry_permitted=False, no_foreign_save_or_close=True)))
        self.actor = self.f.f.successor
        self.addCleanup(patch.stopall)
        patch.object(recovery, 'verify_original_source', return_value={'verified_private_fixture': True}).start()
        self.group = patch.object(recovery, 'group_alive', return_value=False).start()
        self.r = recovery.PreinputRecovery(self.broker.root, readonly=False)
        self.addCleanup(self.r.close)
        self.before = self.broker.get(self.ticket['id'])

    def preview(self):
        return self.r.preview(self.ticket['id'])

    def test_preview_is_readonly_and_binds_complete_original_failure(self):
        before = list(self.broker.db.iterdump())
        value = self.preview()
        self.assertEqual(value['queue_changes'], 0)
        self.assertEqual(value['input_operations'], 0)
        self.assertEqual(value['facts']['ticket'], self.before)
        self.assertEqual(list(self.broker.db.iterdump()), before)

    def test_cli_preview_and_apply_preserve_the_original_run(self):
        argv = ['--root', str(self.broker.root), '--ticket', self.ticket['id']]
        preview_output = io.StringIO()
        with redirect_stdout(preview_output):
            self.assertEqual(recovery.main(['preview', *argv]), 0)
        preview = json.loads(preview_output.getvalue())
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)
        applied_output = io.StringIO()
        with redirect_stdout(applied_output):
            self.assertEqual(recovery.main(['apply', *argv, '--plan-sha256', preview['plan_sha256']]), 0)
        applied = json.loads(applied_output.getvalue())
        self.assertEqual(applied['status'], 'FAILED_PREINPUT')
        self.assertEqual(applied['input_operations'], 0)
        self.assertFalse(applied['original_run_replay'])
        self.assertFalse(applied['legacy_released'])
        after = self.broker.get(self.ticket['id'])
        self.assertEqual(after['invocations'], 1)
        self.assertEqual(json.loads(after['result'])['original_result'], self.before['result'])
        self.assertEqual(self.f.f.legacy.get(self.f.f.original['id']), self.f.f.original)

    def test_cli_refusal_is_structured_and_does_not_write_the_queue(self):
        for suffix in [[], ['--plan-sha256', '0' * 64]]:
            output = io.StringIO()
            with self.subTest(suffix=suffix), redirect_stdout(output):
                self.assertEqual(recovery.main(['apply', '--root', str(self.broker.root),
                    '--ticket', self.ticket['id'], *suffix]), 2)
            refusal = json.loads(output.getvalue())
            self.assertEqual(refusal['outcome'], 'OWNED_PREINPUT_REFUSED')
            self.assertEqual(refusal['input_operations'], 0)
            self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_apply_preserves_invocation_nonce_result_and_legacy_and_cannot_replay(self):
        value = self.preview()
        closed = self.r.apply(self.ticket['id'], value['plan_sha256'])
        after = self.broker.get(self.ticket['id'])
        self.assertEqual(closed['status'], 'FAILED_PREINPUT')
        self.assertEqual(closed['queue_changes'], 1)
        for key in self.before.keys() - {'status', 'result'}:
            self.assertEqual(after[key], self.before[key], key)
        self.assertEqual(json.loads(after['result'])['original_result'], self.before['result'])
        self.assertEqual(self.f.f.legacy.get(self.f.f.original['id']), self.f.f.original)
        self.assertFalse(closed['legacy_released'])
        self.assertFalse(closed['new_run_authorized'])
        with self.assertRaises(ResourceBusy):
            self.r.apply(self.ticket['id'], value['plan_sha256'])
        with self.assertRaises(ResourceBusy):
            self.broker.run(self.ticket['id'], after['token'])

    def test_new_separately_approved_job_can_use_fifo_after_closeout(self):
        self.r.apply(self.ticket['id'], self.preview()['plan_sha256'])
        next_ticket, _plan, _ref, _contract = self.f.request('new-approved-job')
        granted = self.broker.grant(next_ticket['id'])
        self.assertEqual(granted['status'], 'ACTIVE')
        self.assertEqual(granted['invocations'], 0)
        self.assertEqual(self.broker.get(self.ticket['id'])['invocations'], 1)

    def test_wrong_original_workspace_pane_surface_or_thread_cannot_apply(self):
        value = self.preview()
        for key in ('workspace_uuid', 'pane_uuid', 'surface_uuid', 'session_id'):
            self.f.f.identity.return_value = {**self.actor, key: str(uuid.uuid4())}
            with self.subTest(key=key), self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_ACTOR_APPLY'):
                self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_actor_switch_under_original_locks_is_refused(self):
        value = self.preview()
        with patch.object(protocol, 'native_identity', side_effect=[self.actor, {**self.actor, 'session_id': str(uuid.uuid4())}]):
            with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_ACTOR_APPLY'):
                self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_any_application_intent_or_output_or_staging_preserves_pending(self):
        for path in [self.failure.parent / 'native-intent.json', self.failure.parent / 'word-launch-intent.json',
                     Path(self.plan['revision_outputs']['accepted']), Path(self.plan['output']),
                     self.f.stage_parent / ('revision_' + self.plan['stage_id'])]:
            path.write_text('unknown application input')
            try:
                with self.subTest(path=path), self.assertRaises(ResourceBusy):
                    self.preview()
                self.assertEqual(self.broker.get(self.ticket['id']), self.before)
            finally:
                path.unlink()

    def test_failure_flags_or_reason_cannot_be_generalized(self):
        original = self.failure.read_bytes()
        for key, value in [('may_be_open', True), ('owned_document', '/private/owned.docx'),
                           ('reason', 'NATIVE_TIMEOUT'), ('error_type', 'TimeoutExpired')]:
            failure = json.loads(original); failure[key] = value; self.failure.write_text(json.dumps(failure))
            with self.subTest(key=key), self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_FAILURE'):
                self.preview()
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_uncertain_terminal_group_or_changed_original_argv_is_refused(self):
        for key, value in [('process_group_empty', False), ('stdout', 'possible native result'),
                           ('exit_code', 0), ('original_argv', ['/private/replay'])]:
            bad = {**self.original_result, key: value}
            self.broker.db.execute('UPDATE word_restricted_tickets SET result=? WHERE id=?', (json.dumps(bad), self.ticket['id']))
            with self.subTest(key=key), self.assertRaises(ResourceBusy):
                self.preview()
        self.assertEqual(self.broker.get(self.ticket['id'])['status'], 'PENDING')

    def test_live_or_unknown_child_and_held_lock_cannot_close(self):
        self.group.return_value = True
        with self.assertRaisesRegex(ResourceBusy, 'CHILD_ALIVE_OR_UNKNOWN'):
            self.preview()
        self.group.return_value = False
        value = self.preview()
        with os_lock(self.f.f.lock_paths[0]), self.assertRaisesRegex(ResourceBusy, 'OS_LOCK_BUSY'):
            self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_stale_hash_and_replaced_evidence_directory_are_refused(self):
        value = self.preview()
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_PLAN'):
            self.r.apply(self.ticket['id'], '0' * 64)
        parent = self.failure.parent
        displaced = parent.with_name('old-evidence'); parent.rename(displaced); parent.mkdir()
        self.failure.write_bytes((displaced / 'failure.json').read_bytes())
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_PLAN'):
            self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)


class OriginalSourceVerificationTests(unittest.TestCase):
    def test_complete_original_manifest_and_closure_pins_are_required(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        home = Path(temp.name).resolve()
        source = home / '.local/share/multi-agent-collaboration/releases' / recovery.ORIGINAL_RELEASE / 'source'
        current = Path(protocol.__file__).resolve().parents[1]
        shutil.copytree(current, source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        files = {}
        for p in sorted(source.rglob('*')):
            if p.is_file():
                ref = protocol.pin(p)
                files[str(p.relative_to(source))] = dict(bytes=ref['bytes'], sha256=ref['sha256'], executable=os.access(p, os.X_OK))
                p.chmod(0o555 if files[str(p.relative_to(source))]['executable'] else 0o444)
        manifest = source.parent / 'MANIFEST.json'; manifest.write_text(json.dumps(dict(files=files)))
        manifest.chmod(0o444)
        plan = dict(controller_source=str(source), controller_pins={p.name: protocol.pin(p) for p in (source / 'scripts').glob('*.py')})
        critical = {name: plan['controller_pins'][name]['sha256'] for name in recovery.ORIGINAL_DRIVERS}
        with patch.object(Path, 'home', return_value=home), \
                patch.object(recovery, 'ORIGINAL_MANIFEST', protocol.pin(manifest)['sha256']), \
                patch.object(recovery, 'ORIGINAL_DRIVERS', critical):
            self.assertEqual(recovery.verify_original_source(plan)['members'], len(files))
            bad = copy.deepcopy(plan); bad['controller_pins']['resource_broker.py']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ResourceBusy, 'CONTROLLER_PIN_CHANGED'):
                recovery.verify_original_source(bad)
            changed = source / 'scripts/resource_broker.py'
            changed.chmod(0o644)
            changed.write_text('unreviewed original source')
            changed.chmod(0o444)
            with self.assertRaisesRegex(ValueError, 'release member changed'):
                recovery.verify_original_source(plan)


if __name__ == '__main__':
    unittest.main()
