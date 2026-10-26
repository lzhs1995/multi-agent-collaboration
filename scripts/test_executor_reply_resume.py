"""Real file/receipt/identity checks for a resumed original foreground session.

The OS/cmux inventory is isolated. The approval verifier has real immutable-file
fixtures; session readers, CLI, ancestry, locks, lifecycle and scheduler run.
These checks grant no live consumption or installation claim.
"""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import executor_reply_resume as resume
import executor_reask as reask
import executor_reask_lifecycle as lifecycle
import executor_reask_scheduler as scheduler
import cmux_native_delivery as native
import executor_ready as ready
import test_executor_reask_identity as fixtures

CALLER, SESSION, OLD_PID = fixtures.CALLER, fixtures.SESSION, fixtures.PID
NEW_PID = 601


class ResumedReplyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExecutorIdentityIntegrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.approval = self.f.stack.enter_context(patch.object(
            resume, 'approved_controller', side_effect=lambda: dict(
                source=str(Path(reask.__file__).resolve().parents[1]),
                current_sha256='a' * 64, controller=reask.controller())))
        self.f.stack.enter_context(patch.object(native.identity, 'process', side_effect=self.process))

    def process(self, pid, **kwargs):
        if pid == OLD_PID and pid not in self.f.processes:
            raise native.identity.ProcessExited('process absent at initial kernel read')
        return self.f.process(pid, **kwargs)

    def begin_restart(self, *, continuing=True):
        self.f.begin(continuing)
        state = self.f.answered()
        old = self.f.processes.pop(OLD_PID)
        new = dict(copy.deepcopy(old), pid=NEW_PID,
                   birth=[old['birth'][0] + 30, old['birth'][1]])
        self.f.processes[NEW_PID] = new
        self.f.processes[fixtures.SHELL_PID]['ppid'] = NEW_PID
        path = self.f.home / '.claude/sessions' / f'{NEW_PID}.json'
        path.write_text(json.dumps(dict(pid=NEW_PID, kind='interactive', sessionId=SESSION,
            procStart=time.strftime('%a %b %e %H:%M:%S %Y', time.gmtime(new['birth'][0])))))
        path.chmod(0o600)
        self.new_registration = path
        self.original = copy.deepcopy(state)
        self.original_raw = reask.state_path(CALLER).read_bytes()
        self.source = state['controller']['source']
        self.reply_sha = state['reply']['body_sha256']
        self.old_process = old
        return state

    def preview(self, **kwargs):
        args = ['consume-resumed', '--caller-uuid', CALLER,
                '--original-controller', self.source, '--reply-sha256', self.reply_sha]
        if kwargs.get('apply'):
            args.append('--apply')
        if 'plan_sha256' in kwargs:
            args.extend(['--plan-sha256', kwargs['plan_sha256']])
        out = io.StringIO()
        with redirect_stdout(out), patch.object(resume.time, 'time', return_value=1003.):
            code = reask.main(args)
        result = json.loads(out.getvalue())
        self.assertEqual(code, 0 if result['outcome'] in (
            'RESUMED_REPLY_CONSUMPTION_PREVIEW', 'RESUMED_ORIGINAL_REPLY_CONSUMED') else 2)
        return result

    def refused(self, **kwargs):
        before = self.f.files()
        result = self.preview(**kwargs)
        self.assertEqual(result['outcome'], 'RESUMED_CONSUMPTION_REFUSED', result)
        self.assertFalse(result['state_changed'])
        self.assertEqual(self.f.files(), before, 'a refusal must preserve all state and receipts')
        return result

    def test_preview_is_exact_and_zero_write(self):
        self.begin_restart()
        before = self.f.files()
        paths = [p for p in ready.state_root().rglob('*') if p.is_file() and p.suffix != '.lock']
        def stamps():
            return [(p.stat().st_dev, p.stat().st_ino, p.stat().st_size,
                     p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in paths]
        original_stamps = stamps()
        result = self.preview()
        self.assertEqual(result['outcome'], 'RESUMED_REPLY_CONSUMPTION_PREVIEW', result)
        self.assertFalse(result['consumed'])
        self.assertEqual(self.f.files(), before)
        self.assertEqual(stamps(), original_stamps, 'preview must not replace even byte-identical state files')
        plan = result['plan']
        self.assertEqual(plan['state_sha256'], hashlib.sha256(self.original_raw).hexdigest())
        self.assertEqual(plan['resumed_process']['pid'], NEW_PID)
        self.assertEqual(plan['session_id'], SESSION)
        self.assertEqual(plan['original_controller'], self.original['controller'])
        self.assertEqual(plan['reply_sha256'], self.reply_sha)

    def test_exact_apply_preserves_identity_enrollment_pending_and_reservations(self):
        self.begin_restart()
        state = reask.load(CALLER)
        state['terminal_pending'] = {'outcome': 'UNKNOWN', 'marker': 'old-pending'}
        state['paused_tasks'] = [{'id': 'original-reservation', 'owner': fixtures.SUP}]
        reask.save(state)
        before_identity = copy.deepcopy(state['executor_identity'])
        before_enrollment = scheduler.enrollment_path(CALLER).read_bytes()
        before_raw = reask.state_path(CALLER).read_bytes()
        plan = self.preview()
        self.assertEqual(plan['outcome'], 'RESUMED_REPLY_CONSUMPTION_PREVIEW', plan)
        result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_ORIGINAL_REPLY_CONSUMED', result)
        current = reask.load(CALLER)
        self.assertEqual(current['state'], reask.WAITING)
        self.assertEqual(current['rearm_count'], 1)
        self.assertEqual(current['executor_identity'], before_identity)
        self.assertEqual(current['controller'], state['controller'])
        self.assertEqual(current['paused_tasks'], state['paused_tasks'])
        self.assertEqual(current['terminal_pending'], state['terminal_pending'])
        self.assertEqual(scheduler.enrollment_path(CALLER).read_bytes(), before_enrollment)
        receipt = ready.read_json(result['result']['consume_result']['receipt'])
        proof = receipt['executor_authentication']
        self.assertEqual(proof['source'], resume.AUTH_SOURCE)
        self.assertEqual(proof['original_identity'], before_identity)
        self.assertEqual(proof['process']['pid'], NEW_PID)
        self.assertEqual(proof['pre_state']['utf8'].encode(), before_raw)
        self.assertEqual(proof['reviewed_plan_sha256'], plan['plan_sha256'])
        self.assertEqual(receipt['body'], state['reply']['body'])
        self.assertEqual(receipt['body_sha256'], self.reply_sha)

    def test_next_file_round_uses_original_enrollment_and_new_marker(self):
        self.begin_restart()
        enrollment = scheduler.enrollment_path(CALLER).read_bytes()
        old_marker = self.original['reply']['marker']
        plan = self.preview()
        result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_ORIGINAL_REPLY_CONSUMED', result)
        due = reask.load(CALLER)['next_due_epoch']
        self.assertEqual(scheduler.sweep(now=due - 1)['file_posts'], 0)
        self.assertEqual(scheduler.sweep(now=due)['file_posts'], 1)
        current = reask.load(CALLER)
        self.assertNotEqual(current['asks'][-1]['marker'], old_marker)
        self.assertEqual(current['controller'], self.original['controller'])
        self.assertEqual(current['executor_identity'], self.original['executor_identity'])
        original_enrollment = json.loads(enrollment)
        current_enrollment = json.loads(scheduler.enrollment_path(CALLER).read_bytes())
        for key in ('binding', 'registration', 'controller', 'scheduler', 'authorization'):
            self.assertEqual(current_enrollment.get(key), original_enrollment.get(key))

    def test_noncontinuing_reply_consumes_without_rearming(self):
        self.begin_restart(continuing=False)
        plan = self.preview()
        self.assertEqual(plan['outcome'], 'RESUMED_REPLY_CONSUMPTION_PREVIEW', plan)
        result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_ORIGINAL_REPLY_CONSUMED', result)
        self.assertEqual(reask.load(CALLER)['state'], reask.CONSUMED)

    def test_apply_requires_exact_reviewed_plan(self):
        self.begin_restart()
        for sha in ('', '0' * 64):
            with self.subTest(sha=sha):
                self.refused(apply=True, plan_sha256=sha)

    def test_old_process_alive_or_recycled_is_not_resume_authority(self):
        self.begin_restart()
        for delta in ({}, {'birth': [1, 2]},
                      {'birth': [1, 2], 'executable': '/test/unrelated',
                       'argv': ['/test/unrelated'], 'env': {}}):
            with self.subTest(delta=delta):
                self.f.processes[OLD_PID] = dict(self.old_process, **delta)
                result = self.refused()
                self.assertIn('PID is still present', result['error'])
        self.f.processes.pop(OLD_PID)

    def test_same_session_with_replaced_executable_file_identity_is_refused(self):
        self.begin_restart()
        self.f.processes[NEW_PID]['file_identity'][1] += 1
        result = self.refused()
        self.assertIn('same original native Claude executable', result['error'])

    def test_process_timeout_is_not_affirmative_retirement(self):
        self.begin_restart()
        def process(pid, **kwargs):
            if pid == OLD_PID:
                raise TimeoutError('kernel observation unavailable')
            return self.f.process(pid, **kwargs)
        with patch.object(native.identity, 'process', side_effect=process):
            self.refused()

    def test_another_native_session_is_refused(self):
        self.begin_restart()
        body = json.loads(self.new_registration.read_text())
        body['sessionId'] = fixtures.OTHER_SESSION
        self.new_registration.write_text(json.dumps(body))
        self.refused()

    def test_inherited_uuid_or_foreign_model_ancestor_cannot_consume(self):
        self.begin_restart()
        self.f.processes[fixtures.SHELL_PID]['executable'] = '/test/python'
        result = self.refused()
        self.assertIn('not a tool of the original Claude', result['error'])

    def test_changed_pane_or_tty_is_refused(self):
        self.begin_restart()
        for attr, value in (('pane', fixtures.SUP), ('tree_tty', 'ttys999'),
                            ('process_tty', 'ttys999')):
            with self.subTest(attr=attr):
                old = getattr(self.f, attr)
                setattr(self.f, attr, value)
                self.refused()
                setattr(self.f, attr, old)

    def test_replaced_or_modified_original_transcript_is_refused(self):
        self.begin_restart()
        raw = self.f.log.read_bytes()
        self.f.log.write_bytes(b'X' + raw[1:])
        self.refused()
        self.f.log.write_bytes(raw)
        replacement = self.f.log.with_suffix('.replacement')
        replacement.write_bytes(raw)
        replacement.replace(self.f.log)
        self.refused()

    def test_raw_reply_change_and_canonical_hash_mismatch_are_refused(self):
        self.begin_restart()
        path = Path(self.original['reply']['path'])
        raw = path.read_bytes()
        path.write_bytes(raw + b'\n')
        self.refused()

    def test_original_controller_and_reply_scope_are_exact(self):
        self.begin_restart()
        source = self.source
        self.source = '/different/original/controller'
        self.refused()
        self.source = source
        self.reply_sha = 'f' * 64
        self.refused()

    def test_plan_refuses_process_replacement_between_preview_and_apply(self):
        self.begin_restart()
        plan = self.preview()
        self.f.processes[NEW_PID]['birth'][1] += 1
        self.refused(apply=True, plan_sha256=plan['plan_sha256'])

    def test_current_change_before_apply_or_at_commit_is_refused(self):
        self.begin_restart()
        plan = self.preview()
        current = plan['plan']['current']
        changed = dict(current, current_sha256='b' * 64)
        with patch.object(resume, 'approved_controller', return_value=changed):
            self.refused(apply=True, plan_sha256=plan['plan_sha256'])
        with patch.object(resume, 'approved_controller', side_effect=[current, changed]):
            result = self.refused(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertIn('CURRENT changed before consumption', result['error'])

    def test_raw_state_change_at_commit_is_refused_without_receipt(self):
        self.begin_restart()
        plan = self.preview()
        authenticate = resume._resumed_authentication
        observed = []
        def changed(state):
            proof = authenticate(state)
            if not observed:
                current = reask.load(CALLER)
                current['check_count'] += 1
                reask.save(current)
                observed.append(reask.state_path(CALLER).read_bytes())
            return proof
        with patch.object(resume, '_resumed_authentication', side_effect=changed):
            result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_CONSUMPTION_REFUSED', result)
        self.assertIn('changed before consumption', result['error'])
        self.assertEqual(reask.state_path(CALLER).read_bytes(), observed[0])
        self.assertFalse(list((reask.root() / 'consumed').rglob('*.json')))

    def test_authority_expiring_during_apply_refuses_before_receipt(self):
        self.begin_restart()
        state = reask.load(CALLER)
        state['continuation']['until_epoch'] = 1004.
        reask.save(state)
        plan = self.preview()
        before = self.f.files()
        clock = [1003.]
        authenticate = resume._resumed_authentication
        def delayed(state):
            result = authenticate(state)
            clock[0] = 1005.
            return result
        with patch.object(resume.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(resume, '_resumed_authentication', side_effect=delayed):
            result = resume.evaluate(CALLER, self.source, self.reply_sha, apply=True,
                                     plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_CONSUMPTION_REFUSED', result)
        self.assertEqual(self.f.files(), before)

    def test_stop_at_consumption_boundary_is_not_success(self):
        self.begin_restart()
        plan = self.preview()
        consume = lifecycle._consume_authenticated_locked
        def stopped(*args):
            reask.stop_file(CALLER).write_text('{}\n')
            return consume(*args)
        with patch.object(lifecycle, '_consume_authenticated_locked', side_effect=stopped):
            result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_REPLY_NOT_CONSUMED', result)
        self.assertFalse(result['consumed'])
        self.assertEqual(reask.load(CALLER)['state'], reask.STOPPED)
        self.assertFalse(list((reask.root() / 'consumed').rglob('*.json')))

    def test_plan_refuses_state_polling_change(self):
        self.begin_restart()
        plan = self.preview()
        state = reask.load(CALLER)
        state['check_count'] += 1
        reask.save(state)
        result = self.refused(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertIn('exact reviewed', result['error'])

    def test_operator_stop_and_changed_authorization_are_refused(self):
        self.begin_restart()
        reask.stop_file(CALLER).write_text('{}\n')
        self.refused()
        reask.stop_file(CALLER).unlink()
        path = Path(self.original['continuation']['authorization']['path'])
        path.write_text('different authority')
        self.refused()

    def test_absent_episode_never_creates_state_directory_or_lock(self):
        caller = fixtures.OTHER_SESSION
        before = self.f.files()
        result = resume.evaluate(caller, '/missing/original', 'a' * 64, now=1003.)
        self.assertEqual(result['outcome'], 'RESUMED_CONSUMPTION_REFUSED')
        self.assertEqual(self.f.files(), before)
        self.assertFalse(reask.state_path(caller).exists())
        self.assertFalse((reask.root() / (ready.digest(caller) + '.lock')).exists())

    def test_consumption_sync_failure_is_not_reported_as_zero_file_mutation(self):
        self.begin_restart()
        plan = self.preview()
        def committed(path, body):
            ready.write_json(path, body)
            sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            raise reask.CommittedSyncUnknown(path, sha, OSError('fsync unavailable'))
        with patch.object(reask, 'durable_write_json', side_effect=committed):
            result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'COMMITTED_SYNC_UNKNOWN', result)
        self.assertEqual(result['state_changed'], 'UNKNOWN')
        self.assertEqual(result['consumed'], 'UNKNOWN')
        self.assertEqual(reask.load(CALLER)['state'], reask.ANSWERED)
        self.assertTrue(list((reask.root() / 'consumed').rglob('*.json')))

    def test_receipt_sync_retry_retains_bytes_timestamp_and_single_rearm(self):
        self.test_consumption_sync_failure_is_not_reported_as_zero_file_mutation()
        path = next((reask.root() / 'consumed').rglob('*.json'))
        raw = path.read_bytes()
        plan = self.preview()
        with patch.object(ready, 'sync_directory', wraps=ready.sync_directory) as sync, \
                patch.object(reask, 'durable_write_json', wraps=reask.durable_write_json) as writes:
            result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_ORIGINAL_REPLY_CONSUMED', result)
        self.assertEqual(path.read_bytes(), raw)
        sync.assert_any_call(path.parent)
        self.assertFalse(any(Path(call.args[0]) == path for call in writes.call_args_list))
        state = reask.load(CALLER)
        self.assertEqual(state['rearm_count'], 1)
        self.assertEqual(len(state['reply_history']), 1)
        self.assertEqual(state['reply_consumed_epoch'], json.loads(raw)['consumed_epoch'])
        self.refused(apply=True, plan_sha256=plan['plan_sha256'])

    def test_modified_prior_receipt_cannot_be_finalized(self):
        self.test_consumption_sync_failure_is_not_reported_as_zero_file_mutation()
        path = next((reask.root() / 'consumed').rglob('*.json'))
        receipt = ready.read_json(path)
        receipt['body']['task_id'] = 'foreign task'
        ready.write_json(path, receipt)
        plan = self.preview()
        before = self.f.files()
        result = self.preview(apply=True, plan_sha256=plan['plan_sha256'])
        self.assertEqual(result['outcome'], 'RESUMED_CONSUMPTION_STATE_UNKNOWN', result)
        self.assertIn('receipt scope changed: body', result['error'])
        self.assertEqual(self.f.files(), before)


class ReleaseApprovalTests(unittest.TestCase):
    """Run the real guardian file verifier with its actual return structure."""
    def setUp(self):
        import configuration_guardian as guardian
        self.guardian = guardian
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.source = self.home / '.local/share/multi-agent-collaboration/releases/0.4.75-fixture/source'
        contents = {'scripts/configuration_guardian.py': b'# guardian fixture\n',
                    'scripts/manage_install.py': b'# installer fixture\n',
                    'scripts/executor_reply_resume.py': Path(resume.__file__).read_bytes()}
        pins = {}
        for member, raw in contents.items():
            path = self.source / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o444)
            pins[member] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        manifest = self.source.parent / 'MANIFEST.json'
        raw = json.dumps(dict(files=pins)).encode()
        manifest.write_bytes(raw)
        manifest.chmod(0o444)
        self.current = self.home / '.local/state/multi-agent-collaboration/CURRENT.json'
        self.current.parent.mkdir(parents=True)
        self.current.write_text(json.dumps(dict(source=str(self.source),
            manifest_sha256=hashlib.sha256(raw).hexdigest(), python=str(Path(sys.executable).resolve()))))
        for target, name, value in ((Path, 'home', self.home),
                (guardian, '__file__', str(self.source / 'scripts/configuration_guardian.py')),
                (resume, '__file__', str(self.source / 'scripts/executor_reply_resume.py'))):
            context = patch.object(target, name, return_value=value) if name == 'home' else patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)
        controller = patch.object(reask, 'controller', return_value=dict(source=str(self.source)))
        controller.start()
        self.addCleanup(controller.stop)

    def test_real_complete_approval_is_read_only_and_binds_current(self):
        before = {str(p): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        result = resume.approved_controller()
        self.assertEqual(result['source'], str(self.source))
        self.assertEqual(result['current_sha256'], hashlib.sha256(self.current.read_bytes()).hexdigest())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_other_manifest_member_is_refused(self):
        member = self.source / 'scripts/manage_install.py'
        member.chmod(0o644)
        member.write_bytes(b'changed installer\n')
        member.chmod(0o444)
        with self.assertRaisesRegex(ValueError, 'release content mismatch'):
            resume.approved_controller()

    def test_current_change_during_real_verification_is_refused(self):
        verify = self.guardian.verify_release
        def changed(*args):
            result = verify(*args)
            doc = json.loads(self.current.read_text())
            doc['new_setting'] = 'changed during verification'
            self.current.write_text(json.dumps(doc))
            return result
        with patch.object(self.guardian, 'verify_release', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'CURRENT changed during'):
                resume.approved_controller()

    def test_unapproved_cli_refuses_without_creating_any_episode(self):
        entrypoint = self.source / 'scripts/executor_reply_resume.py'
        entrypoint.chmod(0o644)
        reask.root().mkdir(parents=True)
        (reask.root() / (ready.digest(CALLER) + '.lock')).touch(mode=0o600)
        out = io.StringIO()
        with redirect_stdout(out):
            code = reask.main(['consume-resumed', '--caller-uuid', CALLER,
                '--original-controller', str(self.source), '--reply-sha256', 'a' * 64])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())['outcome'], 'RESUMED_CONSUMPTION_REFUSED')
        self.assertIn('release is writable', json.loads(out.getvalue())['error'])
        self.assertFalse(reask.state_path(CALLER).exists())


if __name__ == '__main__':
    unittest.main()
