"""Observed reply wording, archive and status defects through wired entries."""
import contextlib
import io
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

import executor_reask as reask
import executor_reply_advisory as advisory
import executor_proactive_hook as proactive
import scoped_work_completion as scoped
import test_executor_reply_advisory as fixtures


class ReplyCompletionRegressions(unittest.TestCase):
    setUp = fixtures.ReplyAdvisoryTests.setUp
    live = fixtures.ReplyAdvisoryTests.live
    begin = fixtures.ReplyAdvisoryTests.begin
    enroll = fixtures.ReplyAdvisoryTests.enroll
    invoke = fixtures.ReplyAdvisoryTests.invoke
    SESSION = fixtures.ReplyAdvisoryTests.SESSION

    def test_automatic_answered_context_does_not_tell_unconsumed_executor_to_end(self):
        with patch.object(advisory, 'emit_context', wraps=advisory.emit_context):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                advisory.discover(self.payload, now=1003.)
        context = json.loads(output.getvalue())['hookSpecificOutput']['additionalContext']
        self.assertNotIn('end this waiting turn normally', context)
        self.assertIn('UNANSWERED', context)
        self.assertIn('CONSUMED_STANDBY', context)
        self.assertIn('ANSWERED_UNCONSUMED', context)
        self.assertEqual(reask.load(fixtures.CALLER)['state'], reask.ANSWERED)

    def test_archive_same_episode_with_another_owner_is_not_reused(self):
        state = reask.load(fixtures.CALLER)
        target = reask.archive_episode(state)
        original = target.read_bytes()
        other = dict(state, supervisor=fixtures.CALLER)
        with self.assertRaises(ValueError):
            reask.archive_episode(other)
        self.assertEqual(target.read_bytes(), original)

    def test_failed_first_archive_sync_never_exposes_the_final_file(self):
        state = reask.load(fixtures.CALLER)
        target = reask.root() / 'episodes' / reask.ready.digest(state['caller_surface_uuid']) / (
            reask.ready.digest(state['episode_id']) + '.json')
        with patch.object(reask.os, 'fsync', side_effect=OSError('staging sync failed')):
            with self.assertRaises(OSError):
                reask.archive_episode(state)
        self.assertFalse(target.exists())

    def test_status_distinguishes_no_episode_from_unobserved_reservations(self):
        caller = 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(reask.main(['status', '--caller-uuid', caller]), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value['outcome'], 'NO_EPISODE')
        self.assertEqual(value['status_observation']['state_file'], str(reask.state_path(caller)))
        self.assertEqual(value['status_observation']['reservation_scope'], 'NOT_OBSERVED_WITHOUT_EPISODE')
        self.assertEqual(value['status_observation']['input_operations'], 0)

    def test_status_existing_state_reports_exact_snapshot_without_mutation(self):
        path = reask.state_path(fixtures.CALLER)
        before = path.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            reask.main(['status', '--caller-uuid', fixtures.CALLER])
        value = json.loads(output.getvalue())
        self.assertEqual(value['status_observation']['state_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(value['status_observation']['bytes'], len(before))
        self.assertEqual(value['reply_observation']['status'], 'ANSWERED_UNCONSUMED')
        self.assertEqual(value['reply_observation']['next_action'],
                         'FULL_READ_AND_ORIGINAL_SHA_CONSUME')
        self.assertEqual(path.read_bytes(), before)

    def test_status_unreadable_symlink_is_not_an_absent_episode(self):
        path = reask.state_path(fixtures.CALLER)
        target = path.with_suffix('.original')
        path.rename(target)
        path.symlink_to(target)
        original = target.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(reask.main(['status', '--caller-uuid', fixtures.CALLER]), 2)
        value = json.loads(output.getvalue())
        self.assertEqual(value['outcome'], 'STATE_UNREADABLE')
        self.assertEqual(target.read_bytes(), original)

    def test_status_malformed_work_progress_returns_bounded_error_without_mutation(self):
        path = reask.state_path(fixtures.CALLER)
        original = json.loads(path.read_bytes())
        for change in ({'asks':{}}, {'asks':[0]}, {'paused_tasks':{}},
                       {'work_checkpoints':{'x':{'status':'unknown'}}},
                       {'work_checkpoints':{'x':{'status':'IN_PROGRESS','completed_unit_ids':[{}]}}}):
            with self.subTest(change=change):
                path.write_text(json.dumps(dict(original, **change)))
                before = path.read_bytes()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(reask.main(['status', '--caller-uuid', fixtures.CALLER]), 2)
                value = json.loads(output.getvalue())
                self.assertEqual(value['outcome'], 'STATE_UNREADABLE')
                self.assertEqual(value['status_observation']['input_operations'], 0)
                self.assertEqual(path.read_bytes(), before)

    def test_status_corrupt_reply_and_channel_shapes_return_bounded_error(self):
        path = reask.state_path(fixtures.CALLER)
        original = json.loads(path.read_bytes())
        for change in ({'asks': [{'channels': []}]}, {'asks': [{'channels': {'errors': 0}}]},
                       {'reply': ['bad']}, {'last_reply': 'bad'},
                       {'reply_history': [0]}, {'continuation': []}):
            with self.subTest(change=change):
                path.write_text(json.dumps(dict(original, **change)))
                before = path.read_bytes()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = reask.main(['status', '--caller-uuid', fixtures.CALLER])
                value = json.loads(output.getvalue())
                self.assertEqual(code, 2)
                self.assertEqual(value['outcome'], 'STATE_UNREADABLE')
                self.assertEqual(value['status_observation']['input_operations'], 0)
                self.assertEqual(path.read_bytes(), before)

    def test_status_invalid_utf8_returns_bounded_error(self):
        path = reask.state_path(fixtures.CALLER)
        path.write_bytes(b'\xff\xfe')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(reask.main(['status', '--caller-uuid', fixtures.CALLER]), 2)
        self.assertEqual(json.loads(output.getvalue())['outcome'], 'STATE_UNREADABLE')
        self.assertEqual(path.read_bytes(), b'\xff\xfe')

    def test_existing_archive_is_stable_and_rejects_symlink(self):
        state = reask.load(fixtures.CALLER)
        path = reask.archive_episode(state)
        before = path.read_bytes()
        inode = path.stat().st_ino
        self.assertEqual(reask.archive_episode(dict(state, check_count=987)), path)
        self.assertEqual(path.stat().st_ino, inode)
        target = path.with_suffix('.original')
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            reask.archive_episode(state)
        self.assertEqual(target.read_bytes(), before)

    def test_archive_directory_sync_failure_keeps_complete_final_for_exact_inspection(self):
        state = reask.load(fixtures.CALLER)
        actual = reask.os.fsync
        calls = []
        def fail_directory(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError('directory sync failed')
            return actual(fd)
        path = reask.root() / 'episodes' / reask.ready.digest(state['caller_surface_uuid']) / (
            reask.ready.digest(state['episode_id']) + '.json')
        with patch.object(reask.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(OSError):
                reask.archive_episode(state)
        original = path.read_bytes()
        self.assertEqual(json.loads(original), state)
        self.assertEqual(reask.archive_episode(state), path)
        self.assertEqual(path.read_bytes(), original)

    def test_archive_conflicting_controller_does_not_overwrite_original(self):
        state = reask.load(fixtures.CALLER)
        path = reask.archive_episode(state)
        before = path.read_bytes()
        controller = dict(state['controller'], source=str(self.home / 'another-controller'))
        with self.assertRaises(ValueError):
            reask.archive_episode(dict(state, controller=controller))
        self.assertEqual(path.read_bytes(), before)

    def test_archive_two_unrecognized_controller_records_cannot_alias_as_none(self):
        state = dict(reask.load(fixtures.CALLER), controller={'source':'/unknown-one'})
        path = reask.archive_episode(state)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            reask.archive_episode(dict(state, controller={'source':'/unknown-two'}))
        self.assertEqual(path.read_bytes(), before)

    def test_reply_diagnostic_does_not_infer_standby_from_future_state_due(self):
        state = reask.load(fixtures.CALLER)
        waiting = dict(state, state=reask.WAITING, reply=None, next_due_epoch=9999.)
        self.assertEqual(proactive.reply_observation(waiting, now=1100.)['status'], 'UNANSWERED')
        self.assertEqual(proactive.reply_observation(dict(waiting, paused_tasks=[dict(task_id='reserved')]),
                                                    now=1100.)['status'], 'RESERVED_ASSIGNED_WORK')

    def test_no_episode_with_nonregular_archive_parent_fails_without_writing_target(self):
        state = reask.load(fixtures.CALLER)
        parent = reask.root() / 'episodes'
        target = self.home / 'foreign-archive-dir'
        target.mkdir()
        parent.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            reask.archive_episode(state)
        self.assertEqual(list(target.iterdir()), [])


class ScopedCompletionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.order_path = self.root / 'WORK-ORDER.json'
        self.report_path = self.root / 'RESULT.json'
        self.report_path.write_text('{"verdict":"PARTIAL","unexamined":["live"]}\n')
        self.order = dict(schema='scoped-work-order-v1', id='bounded-review',
                          supervisor_surface_uuid=fixtures.SUP, done_marker='BOUNDED_REVIEW_DONE',
                          completion_channel='ORDINARY_NOTICE',
                          outputs=dict(final_output=str(self.report_path),
                                       acceptance=str(self.root / 'ACCEPT.json'),
                                       progress_path=str(self.root / 'PROGRESS.json')))
        canonical = self.root / 'original-controller'
        canonical.mkdir(parents=True)
        (canonical / 'SKILL.md').write_text('Offline canonical installed controller')
        self.controller = canonical
        scripts = canonical / 'scripts'
        scripts.mkdir()
        for name in ('cmux_bridge.py', 'cmux_prompt_reference.py', 'cmux_evidence_io.py'):
            (scripts / name).write_bytes((Path(__file__).resolve().parent / name).read_bytes())
        self.order['controller'] = str(canonical)
        self.order['required_skill'] = dict(path=str(canonical / 'SKILL.md'),
            bytes=(canonical / 'SKILL.md').stat().st_size,
            sha256=hashlib.sha256((canonical / 'SKILL.md').read_bytes()).hexdigest())
        self.home = patch.object(Path, 'home', return_value=self.root)
        self.home.start()
        self.addCleanup(self.home.stop)
        self.write_order()

    def write_order(self):
        self.order_path.write_text(json.dumps(self.order))

    def arguments(self):
        return ['--work-order', str(self.order_path), '--work-order-sha256',
                hashlib.sha256(self.order_path.read_bytes()).hexdigest(), '--report', str(self.report_path),
                '--report-sha256', hashlib.sha256(self.report_path.read_bytes()).hexdigest()]

    def run_cli(self, arguments=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = scoped.main(arguments or self.arguments())
        return code, json.loads(output.getvalue())

    def test_exact_scoped_plan_reads_complete_result_and_sends_nothing(self):
        before = {p: p.read_bytes() for p in (self.order_path, self.report_path)}
        code, result = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(json.loads(result['body'])['report']['bytes'], len(before[self.report_path]))
        self.assertIn('/scripts/cmux_bridge.py', result['submit_command_template'])
        self.assertEqual(shlex.split(result['submit_command_template']), result['submit_argv'])
        self.assertFalse(result['formal_callback'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_explicit_ordinary_domain_schema_keeps_pins_and_original_controller(self):
        self.order.update(schema='spatial43-v8g-increment-review-v22',
                          work_id='spatial43-v8g-increment-review-20261025-c4546-v22')
        self.write_order()
        before = {p: p.read_bytes() for p in (self.order_path, self.report_path)}
        with patch.object(scoped.subprocess, 'run', side_effect=AssertionError('planning must not submit')):
            code, result = self.run_cli()
        self.assertEqual(code, 0, result)
        body = json.loads(result['body'])
        self.assertEqual(body['report']['sha256'], hashlib.sha256(before[self.report_path]).hexdigest())
        self.assertEqual(body['work_order']['sha256'], hashlib.sha256(before[self.order_path]).hexdigest())
        self.assertEqual(result['target_surface_uuid'], self.order['supervisor_surface_uuid'])
        self.assertEqual(result['controller_source'], str(self.controller))
        self.assertEqual(result['submit_argv'][2], str(self.controller / 'scripts/cmux_bridge.py'))
        self.assertEqual(result['input_operations'], 0)
        self.assertFalse(result['body_persisted'])
        self.assertFalse(result['formal_callback'])
        self.assertFalse(result['native_receipt'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_issued_final_path_alias_plans_without_rewriting_order(self):
        self.order['outputs']['final_path'] = self.order['outputs'].pop('final_output')
        self.write_order()
        before = {p: p.read_bytes() for p in (self.order_path, self.report_path)}
        with patch.object(scoped.subprocess, 'run', side_effect=AssertionError('planning must not submit')):
            code, result = self.run_cli()
        self.assertEqual(code, 0, result)
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(result['controller_source'], str(self.controller))
        self.assertEqual(json.loads(result['body'])['report']['sha256'],
                         hashlib.sha256(before[self.report_path]).hexdigest())
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_final_path_retains_alias_conflict_and_stage_separation_checks(self):
        original = dict(self.order['outputs'])
        cases = [
            (dict(original, final_path=str(self.root / 'other-result.json')),
             'final output path aliases disagree'),
            (dict(final_path=str(self.report_path), acceptance=original['acceptance'],
                  progress_path=str(self.report_path)), 'separate absolute paths'),
        ]
        for outputs, reason in cases:
            with self.subTest(reason=reason):
                self.order['outputs'] = outputs
                self.write_order()
                before = {p: p.read_bytes() for p in (self.order_path, self.report_path)}
                code, result = self.run_cli()
                self.assertEqual(code, 2, result)
                self.assertIn(reason, result['error'])
                self.assertEqual(result['input_operations'], 0)
                self.assertNotIn('submit_argv', result)
                self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_domain_schema_requires_explicit_ordinary_channel(self):
        original = dict(self.order, schema='spatial43-v8g-increment-review-v22')
        for channel in (None, 'FORMAL_CALLBACK', 'ordinary_notice', True, {}):
            with self.subTest(channel=channel):
                self.order = dict(original)
                if channel is None:
                    self.order.pop('completion_channel')
                else:
                    self.order['completion_channel'] = channel
                self.write_order()
                code, result = self.run_cli()
                self.assertEqual(code, 2)
                self.assertEqual(result['input_operations'], 0)
                self.assertNotIn('submit_argv', result)

    def test_explicit_domain_notice_cannot_disguise_formal_completion(self):
        original = dict(self.order, schema='spatial43-v8g-increment-review-v22')
        for binding in (dict(task_id='formal-task'), dict(formal_harness_receipt=True),
                        dict(completion_callback='DONE|formal|original'),
                        dict(completion_nonce='original-completion-nonce'),
                        dict(callback_nonce='original-callback-nonce')):
            with self.subTest(binding=binding):
                self.order = dict(original, **binding)
                self.write_order()
                code, result = self.run_cli()
                self.assertEqual(code, 2)
                self.assertEqual(result['input_operations'], 0)
                self.assertNotIn('submit_argv', result)

    def test_legacy_scoped_schema_without_explicit_channel_still_plans(self):
        self.order.pop('completion_channel')
        self.write_order()
        code, result = self.run_cli()
        self.assertEqual(code, 0, result)
        self.assertEqual(result['declared_completion_channel'], 'ORDINARY_NOTICE')
        self.assertEqual(result['input_operations'], 0)

    def test_submit_contract_preserves_both_streams_and_original_reconcile_text(self):
        code, result = self.run_cli()
        self.assertEqual(code, 0)
        contract = result['submit_capture_contract']
        self.assertTrue(contract['capture_stdout'])
        self.assertTrue(contract['capture_stderr'])
        self.assertTrue(contract['preserve_complete_streams'])
        self.assertEqual(contract['original_reconcile_argv'], result['submit_argv'] + ['--reconcile-only'])
        self.assertIn('never resubmit', contract['rc75'])
        import cmux_bridge as bridge
        out, err = io.StringIO(), io.StringIO()
        pending = bridge.DispatchUnconfirmed('pending', state='NATIVE_PENDING',
            details={'attempt':'exact-original-attempt', 'terminal_input_operations':2})
        with patch.object(bridge, 'submit_text', side_effect=pending), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bridge._cli_main(result['submit_argv'][3:])
        self.assertEqual(code, 75)
        self.assertEqual(out.getvalue(), '')
        receipt = json.loads(err.getvalue())
        self.assertEqual(receipt['details']['attempt'], 'exact-original-attempt')
        self.assertEqual(receipt['details']['terminal_input_operations'], 2)
        self.assertEqual(receipt['delivery_state'], 'NATIVE_PENDING')

    def test_generated_command_executes_actual_cli_with_exact_text_and_no_real_transport(self):
        # Execute the generated argv through the real public parser and
        # submit_text and actual journal inline/body checks. Replace only the
        # final input boundary; any
        # typo/unsupported argument must fail before that boundary is reached.
        self.order['id'] = "bounded 'review' $(printf bad) `printf bad` 中文" * 8
        self.write_order()
        code, result = self.run_cli()
        self.assertEqual(code, 0)
        controller = self.controller / 'scripts'
        shutil.copytree(Path(__file__).resolve().parent, controller, dirs_exist_ok=True)
        argv = shlex.split(result['submit_command_template'])
        self.assertEqual(argv[:2], [str(Path(sys.executable).resolve()), '-BE'])
        script = textwrap.dedent('''\
            import json, runpy, sys
            from pathlib import Path
            from unittest.mock import patch
            cli, *cli_args = sys.argv[1:]
            sys.path.insert(0, str(Path(cli).parent))
            import cmux_bridge as bridge
            import cmux_message_journal as journal
            import cmux_native_delivery as native
            import cmux_prompt_reference as reference
            private_home = Path(cli).parents[2]
            Path.home = classmethod(lambda cls: private_home)
            pin = dict(workspace_uuid='11111111-1111-4111-8111-111111111111',
                       caller_surface_uuid='22222222-2222-4222-8222-222222222222',
                       target_surface_uuid=cli_args[cli_args.index('--surface')+1],
                       target_pane_uuid='44444444-4444-4444-8444-444444444444')
            def isolated(surface, text, marker=None, **options):
                reference.require_inline(text)
                body_pin = reference.validate_wire_body(text)
                body_ref = reference.wire_reference(text)
                body = reference.read_body(body_ref)[0] if body_ref else text
                return dict(status='OFFLINE_TRANSPORT_BOUNDARY', surface=surface,
                            marker=marker, text=text, body=body, options=dict(wait_seconds=options['wait_seconds']),
                            body_checked=body_pin is not None, input_operations=0, confirmed=True)
            sys.argv = [cli, *cli_args]
            with patch.object(bridge, 'pin_workspace', return_value=pin), \\
                 patch.object(native, 'bind_target', return_value={'schema':'offline-binding'}), \\
                 patch.object(native, 'marker_binding', side_effect=lambda b,t,m:b), \\
                 patch.object(native, 'receipt_evidence', return_value=True), \\
                 patch.object(bridge, '_submit_text_once', side_effect=isolated):
                code = bridge._cli_main(cli_args)
                raise SystemExit(code)
            ''')
        notice_body = self.root / 'NOTICE.json'
        notice_body.write_bytes(result['body'].encode('utf-8'))
        reference = result['body_reference']
        if reference is not None:
            persist_argv = list(result['persist_body_argv'])
            self.assertEqual(result['persist_body_file_argument_index'], 6)
            self.assertTrue(result['persist_body_template_requires_substitution'])
            self.assertNotIn('<new-exact-notice-body-file>', persist_argv)
            persisted = subprocess.run(persist_argv, input=result['persist_body_stdin'],
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(persisted.returncode, 0, persisted.stderr)
            self.assertEqual(Path(reference['path']).read_bytes(), result['body'].encode('utf-8'))
        completed = subprocess.run(argv[:2] + ['-c', script] + argv[2:],
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observed = json.loads(completed.stdout)
        self.assertEqual(observed['status'], 'OFFLINE_TRANSPORT_BOUNDARY')
        self.assertEqual(observed['surface'], self.order['supervisor_surface_uuid'])
        self.assertEqual(observed['text'], result['wire_text'])
        self.assertEqual(observed['body'], result['body'])
        self.assertTrue(observed['body_checked'])
        self.assertEqual(observed['marker'], self.order['done_marker'])
        self.assertEqual(observed['options']['wait_seconds'], 0.)
        self.assertEqual(observed['input_operations'], 0)
        self.assertTrue(observed['confirmed'])  # Only a fixture receipt, no live transport.

    def test_issued_controller_survives_global_skill_switch(self):
        current = self.root / '.agents/skills/multi-agent-collaboration'
        current.mkdir(parents=True)
        (current / 'SKILL.md').write_text('Unrelated new CURRENT release')
        self.order['id'] = 'long-reviewed-work-' * 80
        self.write_order()
        code, result = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(result['controller_source'], str(self.controller))
        self.assertEqual(result['submit_argv'][2], str(self.controller / 'scripts/cmux_bridge.py'))
        self.assertEqual(result['persist_body_argv_template'][4], str(self.controller / 'scripts'))
        self.assertEqual(result['persist_body_argv'][4], str(self.controller / 'scripts'))
        self.assertTrue(result['controller_skill']['issued_pin_verified'])

    def test_legacy_no_controller_uses_invoked_helper_not_global_link(self):
        self.order.pop('controller')
        self.order.pop('required_skill')
        self.write_order()
        code, result = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(result['controller_source'], str(Path(scoped.__file__).resolve().parent.parent))
        self.assertFalse(result['controller_skill']['issued_pin_verified'])

    def test_controller_alias_or_skill_pin_conflicts_refuse_zero_input(self):
        original = dict(self.order)
        for change in (dict(controller_source=str(self.root / 'foreign-controller')),
                       dict(required_skill=dict(original['required_skill'], sha256='0' * 64)),
                       dict(required_skill=dict(original['required_skill'], bytes=True)),
                       dict(required_skill=dict(original['required_skill'], path=str(self.root / 'SKILL.md')))):
            with self.subTest(change=change):
                self.order = dict(original, **change)
                self.write_order()
                code, result = self.run_cli()
                self.assertEqual(code, 2)
                self.assertEqual(result['input_operations'], 0)
                self.assertNotIn('submit_argv', result)

    def test_progress_and_stage_output_aliases_accept_exact_same_paths(self):
        original = dict(self.order)
        base = original['outputs']
        for outputs in (dict(base, progress=base['progress_path']),
                        dict(final_output=base['final_output'], acceptance=base['acceptance'],
                             progress=base['progress_path']),
                        dict(final_output=base['final_output'], acceptance_output=base['acceptance'],
                             progress_output=base['progress_path'])):
            with self.subTest(outputs=outputs):
                self.order = dict(original, outputs=outputs)
                self.write_order()
                self.assertEqual(self.run_cli()[0], 0)

    def test_top_level_scoped_outputs_accept_without_rewriting_original_order(self):
        paths = self.order.pop('outputs')
        self.order.update(schema='spatial43-scoped-work-order-v4',
                          acceptance_path=paths['acceptance'], progress_path=paths['progress_path'],
                          final_report_path=paths['final_output'])
        self.write_order()
        before = self.order_path.read_bytes()
        code, result = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(self.order_path.read_bytes(), before)
        self.assertEqual(json.loads(result['body'])['report']['path'], paths['final_output'])

    def test_conflicting_output_aliases_refuse_instead_of_choosing_one(self):
        self.order['outputs']['progress'] = str(self.root / 'OTHER-PROGRESS.json')
        self.write_order()
        code, result = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn('progress output path aliases disagree', result['error'])
        self.assertEqual(result['input_operations'], 0)

    def test_prepare_body_cli_persists_exact_reference_without_manual_file_or_input(self):
        self.order['id'] = 'exact-body-' * 90
        self.write_order()
        scripts = self.controller / 'scripts'
        for name in ('cmux_prompt_reference.py', 'cmux_evidence_io.py'):
            (scripts / name).write_bytes((Path(__file__).resolve().parent / name).read_bytes())
        code, result = self.run_cli(self.arguments() + ['--prepare-body'])
        self.assertEqual(code, 0, result)
        self.assertTrue(result['body_persisted'])
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(result['persistence_input_operations'], 0)
        self.assertEqual(Path(result['body_reference']['path']).read_bytes(), result['body'].encode('utf-8'))
        self.assertEqual(result['submit_argv'][2], str(self.controller / 'scripts/cmux_bridge.py'))
        self.assertFalse(result['native_receipt'])

    def test_prepare_body_failure_returns_zero_input_without_submission_arguments(self):
        self.order['id'] = 'exact-body-' * 90
        self.write_order()
        with patch.object(scoped.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, b'', b'')):
            code, result = self.run_cli(self.arguments() + ['--prepare-body'])
        self.assertEqual(code, 2)
        self.assertEqual(result['input_operations'], 0)
        self.assertNotIn('submit_argv', result)

    def test_missing_or_colliding_acceptance_progress_final_paths_are_refused(self):
        original = dict(self.order)
        for outputs in ([], {}, dict(final_output=str(self.report_path)),
                        dict(original['outputs'], acceptance=str(self.report_path)),
                        dict(original['outputs'], progress_path='relative.json'),
                        dict(original['outputs'], progress_path=str(self.root / 'sub' / '..' / 'RESULT.json'))):
            with self.subTest(outputs=outputs):
                self.order = dict(original, outputs=outputs)
                self.write_order()
                self.assertEqual(self.run_cli()[0], 2)

    def test_formal_callback_and_wrong_final_or_sha_are_refused_through_cli(self):
        original = dict(self.order)
        for change in (dict(task_id='formal-task'), dict(formal_harness_receipt=True),
                       dict(completion_callback={'nonce':'abc'}), dict(completion_channel='FORMAL_CALLBACK'),
                       dict(outputs={'final_output':str(self.root / 'OTHER.json')})):
            with self.subTest(change=change):
                self.order = dict(original, **change)
                self.write_order()
                code, result = self.run_cli()
                self.assertEqual(code, 2)
                self.assertEqual(result['input_operations'], 0)
        self.order = original
        self.write_order()
        arguments = self.arguments()
        arguments[-1] = '0' * 64
        self.assertEqual(self.run_cli(arguments)[0], 2)

    def test_symlink_and_oversized_report_cannot_create_a_plan(self):
        arguments = self.arguments()
        target = self.report_path.with_suffix('.original')
        self.report_path.rename(target)
        self.report_path.symlink_to(target)
        self.assertEqual(self.run_cli(arguments)[0], 2)
        self.report_path.unlink()
        with self.report_path.open('wb') as handle:
            handle.truncate(scoped.MAX_REPORT_BYTES + 1)
        self.assertEqual(self.run_cli(self.arguments())[0], 2)


if __name__ == '__main__':
    unittest.main()
