"""Explicit successor registration, immutable old evidence and original identity."""
from contextlib import ExitStack
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cmux_native_delivery as native
import executor_ready as ready
import executor_reask as reask
import executor_reask_identity as identity
import executor_reask_lifecycle as lifecycle
import executor_reask_scheduler as scheduler
import executor_reply_advisory as advisory
import executor_successor_registration as successor

CALLER = '11111111-1111-4111-8111-111111111111'
OLD = '22222222-2222-4222-8222-222222222222'
NEW = '66666666-6666-4666-8666-666666666666'
WORKSPACE = '33333333-3333-4333-8333-333333333333'
PANE = '44444444-4444-4444-8444-444444444444'
SESSION = '55555555-5555-4555-8555-555555555555'
TARGET_SESSION = '77777777-7777-4777-8777-777777777777'


class SuccessorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.stack.enter_context(patch.object(lifecycle, 'ACTIVE_DIR', self.home / 'active'))
        self.stack.enter_context(patch.object(scheduler, 'approved_release', return_value=reask.controller()))
        self.executor = dict(schema=identity.SCHEMA, provider='claude', session_id=SESSION,
            process=dict(pid=123, birth=[1, 2], executable='/original/claude'),
            caller_surface_uuid=CALLER, workspace_uuid=WORKSPACE, pane_uuid=PANE, tty='ttys001')
        self.stack.enter_context(patch.object(identity, 'authenticate',
            side_effect=lambda state: dict(session_id=state['executor_identity']['session_id'],
                process=state['executor_identity']['process'], identity_sha256='a' * 64)))
        self.stack.enter_context(patch.object(advisory, 'capture_executor',
            side_effect=lambda *_args, **_kwargs: copy.deepcopy(self.executor)))
        self.bridge = Mock()
        self.bridge.pin_workspace.side_effect = lambda surface, **_kw: self.live(surface)
        self.stack.enter_context(patch.object(ready, '_bridge', return_value=self.bridge))
        self.target_session = TARGET_SESSION
        self.bind = self.stack.enter_context(patch.object(native, 'bind_target',
            side_effect=lambda _bridge, surface, _text: self.target(surface)))
        self.state = scheduler.start(self.live(OLD), task_id='original-task', now=1000.,
            authenticate_executor=True)
        self.state['terminal_pending'] = dict(marker='held-original', origin=dict(controller='old'), outcome='UNKNOWN')
        self.state['paused_tasks'] = [dict(collaboration_id='original-reservation', task_id='old-business')]
        reask.save(self.state)
        self.auth = self.home / 'successor-authorization.json'
        self.write_authorization()

    def live(self, surface=NEW):
        return dict(caller_surface_uuid=CALLER, target_surface_uuid=surface, workspace_uuid=WORKSPACE,
            caller_pane_uuid=PANE, target_pane_uuid=PANE)

    def target(self, surface):
        return dict(provider='codex', session_id=self.target_session,
            identity=self.live(surface), tty='ttys002',
            process=dict(pid=999, birth=[3, 4], executable='/original/codex'),
            transcript=dict(path=str(self.home / 'native.jsonl'), device=3, inode=4))

    def write_authorization(self, resume=False):
        body = dict(schema=successor.AUTH_SCHEMA, authorization_source='user_message',
            scope='continuing_inquiries_only', caller_surface_uuid=CALLER, executor_session_id=SESSION,
            workspace_uuid=WORKSPACE, original_episode_id=self.state['episode_id'],
            original_supervisor_uuid=self.state['supervisor'], successor_supervisor_uuid=NEW,
            successor_session_id=TARGET_SESSION, overall_task='original-task',
            allow_task_replay=False, allow_shared_writes=False, allow_callback_transfer=False,
            allow_resume_stopped=resume, user_instruction='Continue original executor inquiries with designated successor.',
            until_epoch=2000.)
        self.auth.write_text(json.dumps(body))
        self.auth_sha = hashlib.sha256(self.auth.read_bytes()).hexdigest()

    def preview(self, **kwargs):
        return successor.register(CALLER, NEW, self.auth, self.auth_sha, now=1100., **kwargs)

    def apply(self, preview=None, **kwargs):
        preview = preview or self.preview(**kwargs)
        return successor.register(CALLER, NEW, self.auth, self.auth_sha, apply=True,
            plan_sha256=preview['plan_sha256'], now=1101., **kwargs)

    def test_preview_is_repeatable_zero_input_and_preserves_both_exact_files(self):
        before = (reask.state_path(CALLER).read_bytes(), scheduler.enrollment_path(CALLER).read_bytes())
        first, second = self.preview(), self.preview()
        self.assertEqual(first, second)
        self.assertEqual(first['outcome'], 'SUCCESSOR_READ_ONLY_PLAN')
        self.assertEqual(first['plan']['input_operations'], 0)
        self.assertEqual(before, (reask.state_path(CALLER).read_bytes(), scheduler.enrollment_path(CALLER).read_bytes()))
        self.assertFalse((reask.root() / 'successor-preimages').exists())
        self.bridge._run.assert_not_called()

    def test_apply_preserves_original_bytes_pending_identity_and_reservations(self):
        before_state = reask.state_path(CALLER).read_bytes()
        before_enrollment = scheduler.enrollment_path(CALLER).read_bytes()
        result = self.apply()
        self.assertEqual(result['outcome'], 'SUCCESSOR_INQUIRY_REGISTERED', result)
        current = reask.load(CALLER)
        old = json.loads(before_state)
        self.assertNotEqual(current['episode_id'], old['episode_id'])
        self.assertEqual(current['supervisor'], NEW)
        self.assertEqual(current['executor_identity'], self.executor)
        self.assertEqual(current['terminal_pending'], old['terminal_pending'])
        self.assertEqual(current['paused_tasks'], old['paused_tasks'])
        self.assertTrue(current['file_only_preference'])
        self.assertTrue(current['continuation']['reservation_inquiries'])
        self.assertTrue(current['continuation']['proactive_hook'])
        archive = Path(current['successor_registration']['original_files'])
        self.assertEqual((archive / 'state.json').read_bytes(), before_state)
        self.assertEqual((archive / 'enrollment.json').read_bytes(), before_enrollment)
        self.assertTrue(scheduler.registered(current))
        self.assertEqual(result['input_operations'], 0)
        with patch.object(native, 'bind_target', side_effect=AssertionError('background no native')):
            sweep = scheduler.sweep(now=1102.)
        self.assertEqual(sweep['file_posts'], 1, sweep)
        current = reask.load(CALLER)
        request = ready.read_json(reask.inbox_path(current))
        self.assertEqual(request['successor_supervisor_session_id'], TARGET_SESSION)
        self.assertEqual(request['inquiry_kind'], 'RESERVATION_STATUS')
        self.assertEqual(current['terminal_pending'], old['terminal_pending'])

    def test_changed_original_state_rejects_reviewed_plan_without_registration(self):
        preview = self.preview()
        enrollment = scheduler.enrollment_path(CALLER).read_bytes()
        state = reask.load(CALLER)
        state['purpose'] = 'PENDING_TASK_REPLY'
        reask.save(state)
        before = reask.state_path(CALLER).read_bytes()
        with self.assertRaisesRegex(ValueError, 'PLAN_SHA'):
            self.apply(preview)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.assertEqual(scheduler.enrollment_path(CALLER).read_bytes(), enrollment)

    def test_reviewed_preview_survives_real_observation_only_scheduler_sweep(self):
        preview = self.preview()
        before = reask.state_path(CALLER).read_bytes()
        enrollment_before = scheduler.enrollment_path(CALLER).read_bytes()
        with patch.object(native, 'bind_target', side_effect=AssertionError('file sweep sends no input')):
            swept = scheduler.sweep(now=1100.5)
        self.assertEqual(swept['file_posts'], 0, swept)
        after = reask.state_path(CALLER).read_bytes()
        enrollment_after = scheduler.enrollment_path(CALLER).read_bytes()
        self.assertNotEqual(before, after)
        self.assertNotEqual(enrollment_before, enrollment_after)
        result = self.apply(preview)
        self.assertEqual(result['outcome'], 'SUCCESSOR_INQUIRY_REGISTERED', result)
        current = reask.load(CALLER)
        archive = Path(current['successor_registration']['original_files'])
        self.assertEqual((archive / 'state.json').read_bytes(), after)
        self.assertEqual((archive / 'enrollment.json').read_bytes(), enrollment_after)
        self.assertEqual(current['terminal_pending'], json.loads(after)['terminal_pending'])
        self.bridge._run.assert_not_called()

    def test_changed_target_session_or_authorization_cannot_apply_old_plan(self):
        preview = self.preview()
        before = reask.state_path(CALLER).read_bytes()
        self.target_session = '88888888-8888-4888-8888-888888888888'
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION'):
            self.apply(preview)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.target_session = TARGET_SESSION
        self.auth.write_bytes(self.auth.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'EXACT_SHA'):
            self.apply(preview)

    def test_same_surface_new_supervisor_session_still_creates_explicit_episode(self):
        state = reask.load(CALLER)
        state['supervisor'] = NEW
        reask.save(state)
        self.state = state
        self.write_authorization()
        before = state['episode_id']
        result = self.apply()
        self.assertEqual(result['outcome'], 'SUCCESSOR_INQUIRY_REGISTERED', result)
        self.assertNotEqual(reask.load(CALLER)['episode_id'], before)

    def test_stop_cannot_be_resumed_without_explicit_pinned_authorization(self):
        reask.stop(CALLER, 'USER_STOP')
        before = reask.state_path(CALLER).read_bytes()
        with self.assertRaisesRegex(ValueError, 'STOP_RESUME'):
            self.preview()
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION'):
            self.preview(resume_stopped=True)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.state = reask.load(CALLER)
        self.write_authorization(resume=True)
        stop_raw = reask.stop_file(CALLER).read_bytes()
        result = self.apply(resume_stopped=True)
        self.assertEqual(result['outcome'], 'SUCCESSOR_INQUIRY_REGISTERED', result)
        self.assertFalse(reask.stop_file(CALLER).exists())
        archive = Path(reask.load(CALLER)['successor_registration']['original_files'])
        self.assertEqual((archive / 'stop.json').read_bytes(), stop_raw)

    def test_original_executor_replacement_before_capture_is_refused(self):
        preview = self.preview()
        before = reask.state_path(CALLER).read_bytes()
        replacement = dict(self.executor, session_id=TARGET_SESSION)
        with patch.object(advisory, 'capture_executor', return_value=replacement):
            with self.assertRaisesRegex(ValueError, 'replace the original executor'):
                self.apply(preview)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)

    def test_target_change_at_commit_keeps_uncommitted_transaction_recoverable(self):
        preview = self.preview()
        before = reask.state_path(CALLER).read_bytes()
        old_enrollment = scheduler.enrollment_path(CALLER).read_bytes()
        calls = [0]
        original = self.bind.side_effect
        def change_at_commit(*args):
            calls[0] += 1
            if calls[0] == 3:
                self.target_session = '88888888-8888-4888-8888-888888888888'
            return original(*args)
        with patch.object(native, 'bind_target', side_effect=change_at_commit):
            result = self.apply(preview)
        self.assertEqual(result['outcome'], 'REGISTRATION_UNCOMMITTED', result)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        path = scheduler.enrollment_path(CALLER)
        record = ready.read_json(path)
        self.assertEqual(record['schema'], scheduler.TRANSACTION_SCHEMA)
        scheduler._recover(record, reask.load(CALLER), path)
        self.assertEqual(path.read_bytes(), old_enrollment)

    def test_postcommit_failure_recovers_same_episode_without_replaying_registration(self):
        preview = self.preview()
        with patch.object(scheduler, '_post_commit', side_effect=OSError('directory sync failed')):
            result = self.apply(preview)
        self.assertEqual(result['outcome'], 'COMMITTED_RECOVERY_REQUIRED', result)
        state = reask.load(CALLER)
        self.assertNotEqual(state['episode_id'], self.state['episode_id'])
        path = scheduler.enrollment_path(CALLER)
        scheduler._recover(ready.read_json(path), state, path)
        self.assertEqual(reask.load(CALLER)['episode_id'], state['episode_id'])
        self.assertTrue(scheduler.registered(state))
        self.assertEqual(state['terminal_pending'], self.state['terminal_pending'])

    def test_carried_reservation_only_original_owner_can_release_exactly(self):
        self.apply()
        state = reask.load(CALLER)
        def marker(owner, task='old-business'):
            return dict(collaboration_id='original-reservation', task_id=task, workspace_uuid=WORKSPACE,
                participants=[dict(role='supervisor', surface_uuid=owner),
                    dict(role='executor', surface_uuid=CALLER)])
        before = copy.deepcopy(state)
        self.assertFalse(lifecycle.apply_boundary(state, marker(NEW), 'RELEASED', 1110.))
        self.assertEqual(state, before)
        self.assertFalse(lifecycle.apply_boundary(state, marker(OLD, 'foreign'), 'RELEASED', 1110.))
        self.assertTrue(lifecycle.apply_boundary(state, marker(OLD), 'RELEASED', 1110.))
        self.assertEqual(state['paused_tasks'], [])
        self.assertEqual(state['supervisor'], NEW)
        self.assertEqual(state['terminal_pending'], before['terminal_pending'])

    def test_busy_preview_and_cli_apply_require_exact_plan_without_input(self):
        with reask.episode_lock(CALLER):
            result = self.preview()
        self.assertEqual(result['outcome'], 'EPISODE_BUSY')
        preview = self.preview()
        with patch.object(successor, 'register', return_value=preview) as call, patch('sys.stdout', new_callable=io.StringIO):
            rc = reask.main(['register-successor', '--caller-uuid', CALLER, '--supervisor', NEW,
                '--authorization-file', str(self.auth), '--authorization-sha256', self.auth_sha])
        self.assertEqual(rc, 0)
        self.assertFalse(call.call_args.kwargs['apply'])
        self.bridge._run.assert_not_called()



    def assert_unchanged_refusal(self, preview, pattern='SUCCESSOR_'):
        state_path, enrollment_path = reask.state_path(CALLER), scheduler.enrollment_path(CALLER)
        before = (state_path.read_bytes(), enrollment_path.read_bytes(),
                  scheduler._raw(reask.stop_file(CALLER)))
        with self.assertRaisesRegex(ValueError, pattern):
            self.apply(preview)
        self.assertEqual(before, (state_path.read_bytes(), enrollment_path.read_bytes(),
                                 scheduler._raw(reask.stop_file(CALLER))))
        self.assertFalse((reask.root() / 'successor-preimages').exists())
        self.bridge._run.assert_not_called()

    def test_all_semantic_changes_refuse_reviewed_sha_without_writing(self):
        changes = {
            'state': reask.ANSWERED, 'supervisor': NEW, 'workspace_uuid': PANE,
            'episode_id': 'changed-episode', 'task_id': 'changed-task',
            'purpose': 'PENDING_TASK_REPLY', 'terminal_pending': {'marker': 'changed', 'outcome': 'UNKNOWN'},
            'terminal_attempts': 1, 'paused_tasks': [], 'reply': {'body': 'new-reply'},
            'last_reply': {'sha256': 'b' * 64}, 'reply_history': [{'marker': 'new'}],
            'replies_closed_through': 1, 'asks': [{'marker': 'new'}], 'attempt_count': 1,
            'ask_count': 1, 'file_posts': 1, 'unknown_observation': {'authority': True},
        }
        original = reask.state_path(CALLER).read_bytes()
        preview = self.preview()
        for key, value in changes.items():
            with self.subTest(field=key):
                state = json.loads(original)
                state[key] = value
                reask.save(state)
                self.assert_unchanged_refusal(preview)
                reask.state_path(CALLER).write_bytes(original)
        for key in ('paused_tasks', 'terminal_pending', 'reply'):
            with self.subTest(removed=key):
                state = json.loads(original)
                state.pop(key, None)
                if key == 'reply':
                    state['continuation'] = {'status': 'REVOKED'}
                reask.save(state)
                self.assert_unchanged_refusal(preview)
                reask.state_path(CALLER).write_bytes(original)

    def test_controller_and_enrollment_authority_changes_refuse_before_archive(self):
        preview = self.preview()
        original_state = reask.state_path(CALLER).read_bytes()
        state = json.loads(original_state)
        state['controller']['files']['scripts/executor_reask.py'] = 'b' * 64
        reask.save(state)
        self.assert_unchanged_refusal(preview, 'CONTROLLER_UNVERIFIED')
        reask.state_path(CALLER).write_bytes(original_state)
        path = scheduler.enrollment_path(CALLER)
        original = path.read_bytes()
        for key, value in {
            'authorization': 'FORGED_START', 'status': 'FINISHED',
            'registration': {'token': 'c' * 32}, 'unknown': 'keep-hashed',
            'release_resume_observation': {'state': 'RELEASED'},
        }.items():
            with self.subTest(field=key):
                record = json.loads(original)
                record[key] = value
                path.write_text(json.dumps(record))
                self.assert_unchanged_refusal(preview)
                path.write_bytes(original)

    def test_all_valid_poll_fields_can_drift_without_changing_semantic_plan(self):
        state = reask.load(CALLER)
        state.update(check_count=3, attempt_count=10, marker_secret='s',
                     reply_scan_ordinal=1, last_poll=dict(at_epoch=1090., duration_seconds=.01))
        reask.save(state)
        preview = self.preview()
        state.update(check_count=4, reply_scan_ordinal=9,
                     last_poll=dict(at_epoch=1100.5, duration_seconds=.2))
        reask.save(state)
        after = reask.state_path(CALLER).read_bytes()
        result = self.apply(preview)
        self.assertEqual(result['outcome'], 'SUCCESSOR_INQUIRY_REGISTERED', result)
        archive = Path(reask.load(CALLER)['successor_registration']['original_files'])
        self.assertEqual((archive / 'state.json').read_bytes(), after)
        self.bridge._run.assert_not_called()

    def test_malformed_and_unknown_poll_observations_do_not_disappear(self):
        preview = self.preview()
        original = reask.state_path(CALLER).read_bytes()
        changes = [
            ('check_count', True), ('check_count', -1), ('check_count', 1.5),
            ('reply_scan_ordinal', 0), ('reply_scan_ordinal', 1),
            ('last_poll', None), ('last_poll', {}),
            ('last_poll', {'at_epoch': 1100.}),
            ('last_poll', {'at_epoch': True, 'duration_seconds': .1}),
            ('last_poll', {'at_epoch': 1200., 'duration_seconds': .1}),
            ('last_poll', {'at_epoch': 1100., 'duration_seconds': -1}),
            ('last_poll', {'at_epoch': 1100., 'duration_seconds': .1, 'unknown': True}),
        ]
        for key, value in changes:
            with self.subTest(field=key, value=value):
                state = json.loads(original)
                state[key] = value
                reask.save(state)
                self.assert_unchanged_refusal(preview)
                reask.state_path(CALLER).write_bytes(original)
        for suffix in (', "check_count": 0', ', "unknown": NaN', ', "unknown": 1e999'):
            with self.subTest(suffix=suffix):
                reask.state_path(CALLER).write_bytes(original[:-1] + suffix.encode() + b'}')
                self.assert_unchanged_refusal(preview, 'SUCCESSOR_|Out of range')
                reask.state_path(CALLER).write_bytes(original)

    def test_execution_producer_unknown_fields_and_nonzero_posts_remain_semantic(self):
        with patch.object(native, 'bind_target', side_effect=AssertionError('file-only sweep')):
            scheduler.sweep(now=1100.)
        preview = self.preview()
        path = scheduler.enrollment_path(CALLER)
        original = path.read_bytes()
        for key, value in {
            'source': '/different/source', 'scheduler': dict(scheduler.provenance(), sha256='b' * 64),
            'state': reask.ANSWERED, 'file_posts': 1, 'input_operations': 1,
            'unknown': {'allow': True}, 'at_epoch': 1200.,
        }.items():
            with self.subTest(field=key):
                record = json.loads(original)
                record['last_execution'][key] = value
                path.write_text(json.dumps(record))
                self.assert_unchanged_refusal(preview)
                path.write_bytes(original)

    def test_existing_nonzero_total_accepts_another_zero_post_real_sweep(self):
        state = reask.load(CALLER)
        state['file_posts'] = 5
        reask.save(state)
        with patch.object(native, 'bind_target', side_effect=AssertionError('file-only sweep')):
            first = scheduler.sweep(now=1100.)
        self.assertEqual(first['file_posts'], 0, first)
        preview = self.preview()
        with patch.object(native, 'bind_target', side_effect=AssertionError('file-only sweep')):
            second = scheduler.sweep(now=1100.5)
        self.assertEqual(second['file_posts'], 0, second)
        result = self.apply(preview)
        self.assertEqual(result['outcome'], 'SUCCESSOR_INQUIRY_REGISTERED', result)

    def test_actual_new_file_post_invalidates_reviewed_plan(self):
        state = reask.load(CALLER)
        state['paused_tasks'] = []
        reask.save(state)
        preview = self.preview()
        with patch.object(native, 'bind_target', side_effect=AssertionError('file-only sweep')):
            swept = scheduler.sweep(now=1100.5)
        self.assertEqual(swept['file_posts'], 1, swept)
        self.assert_unchanged_refusal(preview)

    def test_stop_expiry_and_exact_authorization_bytes_still_refuse(self):
        preview = self.preview()
        reask.stop(CALLER, 'USER_STOP')
        self.assert_unchanged_refusal(preview, 'STOP_RESUME')
        reask.stop_file(CALLER).unlink()
        reask.state_path(CALLER).write_text(json.dumps(self.state))
        preview = self.preview()
        auth = self.auth.read_bytes()
        self.auth.write_bytes(auth + b' ')
        self.assert_unchanged_refusal(preview, 'EXACT_SHA')
        self.auth.write_bytes(auth)
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION'):
            successor.register(CALLER, NEW, self.auth, self.auth_sha, apply=True,
                               plan_sha256=preview['plan_sha256'], now=2000.)

    def test_late_raw_state_and_stop_changes_keep_uncommitted_intent(self):
        for change in ('state', 'stop'):
            with self.subTest(change=change):
                preview = self.preview()
                old_enrollment = scheduler.enrollment_path(CALLER).read_bytes()
                before = reask.state_path(CALLER).read_bytes()
                original = successor.validate_commit
                def changed(*args, **kwargs):
                    if change == 'state':
                        reask.state_path(CALLER).write_bytes(before + b' ')
                    else:
                        ready.write_json(reask.stop_file(CALLER), dict(reason='USER_STOP'))
                    return original(*args, **kwargs)
                with patch.object(successor, 'validate_commit', side_effect=changed):
                    result = self.apply(preview)
                self.assertEqual(result['outcome'], 'REGISTRATION_UNCOMMITTED', result)
                self.assertEqual(reask.state_path(CALLER).read_bytes(),
                                 before + b' ' if change == 'state' else before)
                path = scheduler.enrollment_path(CALLER)
                envelope = ready.read_json(path)
                self.assertEqual(envelope['schema'], scheduler.TRANSACTION_SCHEMA)
                reask.state_path(CALLER).write_bytes(before)
                reask.stop_file(CALLER).unlink(missing_ok=True)
                scheduler._recover(envelope, reask.load(CALLER), path)
                self.assertEqual(path.read_bytes(), old_enrollment)
                self.bridge._run.assert_not_called()

    def test_envelope_preimage_tampering_cannot_commit(self):
        preview = self.preview()
        before = reask.state_path(CALLER).read_bytes()
        path = scheduler.enrollment_path(CALLER)
        original = successor.validate_commit
        def changed(*args, **kwargs):
            envelope = ready.read_json(path)
            envelope['previous_enrollment_sha256'] = 'b' * 64
            ready.write_json(path, envelope)
            return original(*args, **kwargs)
        with patch.object(successor, 'validate_commit', side_effect=changed):
            result = self.apply(preview)
        self.assertEqual(result['outcome'], 'REGISTRATION_UNCOMMITTED', result)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.bridge._run.assert_not_called()

    def test_raw_drift_during_final_native_authentication_refuses_commit(self):
        preview = self.preview()
        before = reask.state_path(CALLER).read_bytes()
        path = scheduler.enrollment_path(CALLER)
        calls = [0]
        original = self.bind.side_effect
        def changed(*args):
            calls[0] += 1
            if calls[0] == 3:
                path.write_bytes(path.read_bytes() + b' ')
            return original(*args)
        with patch.object(native, 'bind_target', side_effect=changed):
            result = self.apply(preview)
        self.assertEqual(result['outcome'], 'REGISTRATION_UNCOMMITTED', result)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.bridge._run.assert_not_called()



    def cli_register(self, preview=None):
        argv = ['register-successor', '--caller-uuid', CALLER, '--supervisor', NEW,
                '--authorization-file', str(self.auth), '--authorization-sha256', self.auth_sha]
        if preview is not None:
            argv += ['--apply', '--plan-sha256', preview['plan_sha256']]
        with patch('sys.stdout', new_callable=io.StringIO) as output, \
             patch.object(successor.time, 'time', return_value=1101.):
            rc = reask.main(argv)
        return rc, json.loads(output.getvalue())

    def test_wired_cli_reviewed_apply_survives_observation_sweep(self):
        rc, preview = self.cli_register()
        self.assertEqual(rc, 0)
        with patch.object(native, 'bind_target', side_effect=AssertionError('file-only sweep')):
            swept = scheduler.sweep(now=1100.5)
        self.assertEqual(swept['file_posts'], 0, swept)
        rc, result = self.cli_register(preview)
        self.assertEqual(rc, 0)
        self.assertEqual(result['outcome'], 'SUCCESSOR_INQUIRY_REGISTERED', result)
        self.bridge._run.assert_not_called()

    def test_wired_cli_semantic_negative_is_not_discarded_with_poll_fields(self):
        rc, preview = self.cli_register()
        self.assertEqual(rc, 0)
        state = reask.load(CALLER)
        state['terminal_pending']['outcome'] = 'RECEIVED'
        reask.save(state)
        before = reask.state_path(CALLER).read_bytes()
        with self.assertRaisesRegex(ValueError, 'REVIEWED_PLAN_SHA'):
            self.cli_register(preview)
        self.assertEqual(reask.state_path(CALLER).read_bytes(), before)
        self.bridge._run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
