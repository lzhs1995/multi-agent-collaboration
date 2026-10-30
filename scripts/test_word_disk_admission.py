"""Disk refusals preserve execution budgets and exact original failure evidence."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from resource_broker import ResourceBusy
import word_preinput_recovery as recovery
import word_restricted_broker as protocol
import word_restricted_pdf as pdf
import test_word_preinput_recovery as preinput_tests
import test_word_restricted_revision as owned_tests


class BeforeBudgetTests(unittest.TestCase):
    def setUp(self):
        self.f = owned_tests.OwnedRevisionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.broker = self.f.f.broker

    def test_low_space_grant_preserves_waiting_row_and_fifo(self):
        ticket, plan, _ref, _contract = self.f.request()
        self.f.f.disk.return_value = plan['required_free_bytes'] - 1
        before = self.broker.get(ticket['id'])
        with self.assertRaisesRegex(ResourceBusy, 'WORD_DISK_ADMISSION_CHANGED'):
            self.broker.grant(ticket['id'])
        self.assertEqual(self.broker.get(ticket['id']), before)
        self.assertEqual(before['status'], 'WAITING')
        self.assertIsNone(before['token'])
        self.assertEqual(before['invocations'], 0)

    def test_low_space_run_consumes_no_nonce_child_or_invocation(self):
        ticket, plan, _ref, _contract = self.f.request()
        granted = self.broker.grant(ticket['id'])
        self.f.f.disk.return_value = plan['required_free_bytes'] - 1
        with patch.object(protocol.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(ResourceBusy, 'WORD_DISK_ADMISSION_CHANGED'):
                self.broker.run(ticket['id'], granted['token'])
            launch.assert_not_called()
        self.assertEqual(self.broker.get(ticket['id']), granted)
        self.assertIsNone(granted['nonce'])
        self.assertIsNone(granted['parent_pid'])
        self.assertIsNone(granted['pgid'])
        self.assertFalse(Path(plan['evidence_root']).exists())

    def test_restored_space_uses_the_same_unconsumed_lease_once(self):
        ticket, plan, ref, _contract = self.f.request()
        granted = self.broker.grant(ticket['id'])
        self.f.f.disk.return_value = plan['required_free_bytes'] - 1
        with self.assertRaises(ResourceBusy):
            self.broker.run(ticket['id'], granted['token'])
        self.f.f.disk.return_value = plan['required_free_bytes']
        with patch.object(protocol.subprocess, 'Popen', self.f.child(ticket, plan, ref)):
            result = self.broker.run(ticket['id'], granted['token'])
        self.assertEqual(result['status'], 'COMPLETE_WITH_FOREIGN_UNKNOWN')
        after = self.broker.get(ticket['id'])
        self.assertEqual(after['invocations'], 1)
        self.assertEqual(after['expires'], granted['expires'])
        self.assertEqual(after['token'], granted['token'])
        with self.assertRaises(ResourceBusy):
            self.broker.run(ticket['id'], granted['token'])

    def test_declared_reserve_is_used_instead_of_only_the_global_floor(self):
        job, job_ref = self.f.job('larger-reserve')
        disposition, _contract = self.f.disposition(job_ref, 'larger-reserve')
        plan, _old_ref = self.f.plan(job, job_ref, 'larger-reserve')
        plan['required_free_bytes'] = protocol.FLOOR + 1024**3
        ref = self.f.f.write('larger-plan.json', plan)
        ticket = self.broker.request(disposition, ref)
        self.f.f.disk.return_value = protocol.FLOOR
        before = self.broker.get(ticket['id'])
        with self.assertRaisesRegex(ResourceBusy, 'WORD_DISK_ADMISSION_CHANGED'):
            self.broker.grant(ticket['id'])
        self.assertEqual(self.broker.get(ticket['id']), before)

    def test_disk_query_failure_preserves_original_budget(self):
        ticket, _plan, _ref, _contract = self.f.request()
        granted = self.broker.grant(ticket['id'])
        self.f.f.disk.side_effect = ResourceBusy('WORD_DISK_ADMISSION_UNAVAILABLE')
        with self.assertRaisesRegex(ResourceBusy, 'WORD_DISK_ADMISSION_UNAVAILABLE'):
            self.broker.run(ticket['id'], granted['token'])
        self.assertEqual(self.broker.get(ticket['id']), granted)

    def test_in_child_space_guard_remains_before_any_application_stage(self):
        _ticket, plan, _ref, _contract = self.f.request()
        args = SimpleNamespace(ticket='private', token='private-token', nonce='private-nonce')
        child_broker = SimpleNamespace(require=lambda *a, **kw: ({'nonce': args.nonce}, plan))
        driver = pdf.Driver(protocol, child_broker, args, {}, plan)
        with patch.object(pdf.shutil, 'disk_usage', return_value=SimpleNamespace(
                free=plan['required_free_bytes'] - 1)):
            with self.assertRaisesRegex(ResourceBusy, 'WORD_DISK_ADMISSION_CHANGED'):
                driver.guard()
        self.assertIsNone(driver.session)


class DiskObservationTests(unittest.TestCase):
    def test_output_volume_is_observed_and_declared_threshold_is_exact(self):
        plan = {'evidence_root': '/private/run/evidence/restricted-native', 'required_free_bytes': protocol.FLOOR}
        with patch.object(protocol.shutil, 'disk_usage', return_value=SimpleNamespace(
                free=protocol.FLOOR)) as query:
            protocol.verify_disk_admission(plan)
        query.assert_called_once_with(Path('/private/run/evidence'))

    def test_unavailable_or_invalid_disk_snapshot_refuses(self):
        plan = {'evidence_root': '/private/evidence/new', 'required_free_bytes': protocol.FLOOR}
        with patch.object(protocol.shutil, 'disk_usage', side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(ResourceBusy, 'WORD_DISK_ADMISSION_UNAVAILABLE'):
                protocol.disk_free_bytes(plan)
        for value in (-1, None, True, float(protocol.FLOOR)):
            with self.subTest(value=value), patch.object(protocol.shutil, 'disk_usage',
                    return_value=SimpleNamespace(free=value)):
                with self.assertRaisesRegex(ResourceBusy, 'WORD_DISK_ADMISSION_UNAVAILABLE'):
                    protocol.disk_free_bytes(plan)


class DiskPreinputTests(unittest.TestCase):
    def setUp(self):
        self.f = preinput_tests.PreinputRecoveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.broker, self.ticket = self.f.broker, self.f.ticket
        self.f.original_result['stderr'] = json.dumps(dict(status='RESTRICTED_NATIVE_PENDING',
            reason=recovery.DISK_REASON, error_type='ResourceBusy', no_retry=True,
            no_foreign_save_or_close=True, legacy_released=False))
        self.broker.db.execute('UPDATE word_restricted_tickets SET result=? WHERE id=?',
            (json.dumps(self.f.original_result), self.ticket['id']))
        failure = json.loads(self.f.failure.read_bytes()); failure['reason'] = recovery.DISK_REASON
        self.f.failure.write_text(json.dumps(failure))
        recovery.verify_original_source.return_value = {
            'preinput_reason': recovery.DISK_REASON, 'failure_profile': recovery.DISK_PROFILE}
        self.before = self.broker.get(self.ticket['id'])

    def test_closeout_preserves_failed_run_and_never_refunds_or_releases(self):
        before_failure = self.f.failure.read_bytes()
        preview = self.f.r.preview(self.ticket['id'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)
        closed = self.f.r.apply(self.ticket['id'], preview['plan_sha256'])
        after = self.broker.get(self.ticket['id'])
        self.assertEqual(after['status'], 'FAILED_PREINPUT')
        for key in self.before.keys() - {'status', 'result'}:
            self.assertEqual(after[key], self.before[key], key)
        self.assertEqual(json.loads(after['result'])['original_result'], self.before['result'])
        self.assertEqual(self.f.failure.read_bytes(), before_failure)
        self.assertFalse(closed['new_run_authorized'])
        self.assertFalse(closed['legacy_released'])
        self.assertEqual(closed['input_operations'], 0)
        with self.assertRaises(ResourceBusy):
            self.broker.run(self.ticket['id'], after['token'])

    def test_possible_application_stage_or_output_keeps_disk_failure_pending(self):
        stage = self.f.f.stage_parent / ('revision_' + self.f.plan['stage_id'])
        for path in [self.f.failure.parent / 'native-intent.json',
                     self.f.failure.parent / 'PRE-inventory.json',
                     *map(Path, self.f.plan['revision_outputs'].values()),
                     Path(self.f.plan['output']), stage]:
            path.write_text('unconfirmed application effect')
            try:
                with self.subTest(path=path), self.assertRaises(ResourceBusy):
                    self.f.r.preview(self.ticket['id'])
                self.assertEqual(self.broker.get(self.ticket['id']), self.before)
            finally:
                path.unlink()

    def test_only_original_actor_and_exact_preimage_can_close(self):
        preview = self.f.r.preview(self.ticket['id'])
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_PLAN'):
            self.f.r.apply(self.ticket['id'], '0' * 64)
        for key in ('surface_uuid', 'pane_uuid', 'workspace_uuid', 'session_id'):
            self.f.f.f.identity.return_value = {**self.f.actor, key: 'different-original-scope'}
            with self.subTest(key=key), self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_ACTOR_APPLY'):
                self.f.r.apply(self.ticket['id'], preview['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_uncertain_child_or_claimed_open_document_is_not_preinput(self):
        self.f.group.return_value = True
        with self.assertRaisesRegex(ResourceBusy, 'CHILD_ALIVE_OR_UNKNOWN'):
            self.f.r.preview(self.ticket['id'])
        self.f.group.return_value = False
        failure = json.loads(self.f.failure.read_bytes()); failure['may_be_open'] = True
        self.f.failure.write_text(json.dumps(failure))
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_FAILURE'):
            self.f.r.preview(self.ticket['id'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)


class OriginalApprovalDelegationTests(unittest.TestCase):
    def setUp(self):
        self.f = owned_tests.OwnedRevisionTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.ticket, self.plan, _ref, contract_ref = self.f.request()
        self.broker = self.f.f.broker
        contract = json.loads(protocol.read_pin(contract_ref))
        contract.update(schema=protocol.NATIVE_REVISION_SCHEMA,
            native_approval_source=self.plan['controller_pins']['word_native_approval.py'])
        self.ref = self.f.f.write('original-native-contract.json', contract)
        self.broker.db.execute('UPDATE word_restricted_revision_dispositions SET contract=? WHERE id=?',
            (json.dumps(self.ref), self.ticket['disposition']))
        self.contract = contract
        self.r = recovery.PreinputRecovery(self.broker.root, readonly=False)
        self.addCleanup(self.r.close)
        self.profile = {'preinput_reason': recovery.DISK_REASON, 'failure_profile': recovery.DISK_PROFILE}
        drivers = {name: self.plan['controller_pins'][name]['sha256'] for name in recovery.DISK_DRIVERS}
        change = patch.object(recovery, 'DISK_DRIVERS', drivers); change.start(); self.addCleanup(change.stop)
        self.result = SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(dict(
            outcome='RESTRICTED_WORD_READ_ONLY_PLAN', contract=self.ref, input_operations=0,
            queue_changes=0, legacy_released=False, plan_sha256=protocol.digest(dict(
                contract=self.ref, legacy=contract['original_row_sha256'])))))

    def call(self):
        return self.r.original_contract(self.ticket, self.plan, self.profile)

    def test_only_exact_original_broker_readonly_preview_is_delegated(self):
        before = list(self.broker.db.iterdump())
        with patch.object(recovery.subprocess, 'run', return_value=self.result) as run:
            self.assertEqual(self.call(), self.contract)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:], ['-BE', self.plan['controller_pins']['word_restricted_broker.py']['path'],
            'preview', '--root', str(self.broker.root), '--file', self.ref['path'], '--sha256', self.ref['sha256']])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertEqual(list(self.broker.db.iterdump()), before)

    def test_nonzero_or_stderr_delegation_keeps_queue_unchanged(self):
        before = list(self.broker.db.iterdump())
        for result in [SimpleNamespace(returncode=2, stderr='', stdout=self.result.stdout),
                       SimpleNamespace(returncode=0, stderr='unknown diagnostic', stdout=self.result.stdout)]:
            with self.subTest(result=result), patch.object(recovery.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_NATIVE_APPROVAL_PREVIEW_REFUSED'):
                    self.call()
        self.assertEqual(list(self.broker.db.iterdump()), before)

    def test_claimed_input_or_wrong_contract_is_not_readonly_validation(self):
        original = json.loads(self.result.stdout)
        for key, value in [('input_operations', 1), ('queue_changes', 1), ('legacy_released', True),
                           ('contract', {}), ('plan_sha256', '0' * 64)]:
            out = dict(original); out[key] = value
            result = SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(out))
            with self.subTest(key=key), patch.object(recovery.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(ResourceBusy, 'EXACT_ORIGINAL_READ_ONLY_APPROVAL_PREVIEW'):
                    self.call()

    def test_changed_original_approval_source_refuses_before_delegation(self):
        bad = dict(self.contract, native_approval_source={'path': '/foreign/approval.py'})
        ref = self.f.f.write('wrong-native-source.json', bad)
        self.broker.db.execute('UPDATE word_restricted_revision_dispositions SET contract=? WHERE id=?',
            (json.dumps(ref), self.ticket['disposition']))
        with patch.object(recovery.subprocess, 'run') as run:
            with self.assertRaisesRegex(ResourceBusy, 'EXACT_ORIGINAL_NATIVE_APPROVAL_CONTROLLER'):
                self.call()
            run.assert_not_called()

    def test_delegation_timeout_retains_unknown_and_original_queue(self):
        before = list(self.broker.db.iterdump())
        with patch.object(recovery.subprocess, 'run', side_effect=subprocess.TimeoutExpired('readonly-preview', 30)):
            with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_NATIVE_APPROVAL_PREVIEW_UNAVAILABLE'):
                self.call()
        self.assertEqual(list(self.broker.db.iterdump()), before)


class DiskSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name).resolve() / 'source'
        scripts = self.source / 'scripts'; scripts.mkdir(parents=True)
        names = [*recovery.DISK_DRIVERS, 'resource_broker.py']
        for name in names:
            (scripts / name).write_text('# private complete source fixture: ' + name + '\n')
        (self.source / 'VERSION').write_text('0.4.82\n')
        files = {}
        for path in self.source.rglob('*'):
            if path.is_file():
                ref = protocol.pin(path)
                files[path.relative_to(self.source).as_posix()] = {
                    'bytes': ref['bytes'], 'sha256': ref['sha256'], 'executable': False}
                path.chmod(0o444)
        self.plan = dict(controller_source=str(self.source),
            controller_pins={p.name: protocol.pin(p) for p in scripts.glob('*.py')})
        values = {'DISK_SOURCE_COUNT': len(files),
                  'DISK_SOURCE_BYTES': sum(v['bytes'] for v in files.values()),
                  'DISK_SOURCE_DIGEST': protocol.digest(files),
                  'DISK_DRIVERS': {name: self.plan['controller_pins'][name]['sha256']
                                   for name in recovery.DISK_DRIVERS}}
        for name, value in values.items():
            change = patch.object(recovery, name, value); change.start(); self.addCleanup(change.stop)

    def test_exact_whole_source_selects_only_disk_failure_profile(self):
        value = recovery.verify_original_source(self.plan)
        self.assertEqual(value['preinput_reason'], recovery.DISK_REASON)
        self.assertEqual(value['failure_profile'], recovery.DISK_PROFILE)
        self.assertEqual(value['members'], recovery.DISK_SOURCE_COUNT)

    def test_changed_member_or_extra_member_cannot_look_like_original_82(self):
        path = self.source / 'scripts/resource_broker.py'
        raw = path.read_bytes(); path.chmod(0o644); path.write_bytes(raw + b'# changed\n'); path.chmod(0o444)
        with self.assertRaises(ResourceBusy):
            recovery.verify_original_source(self.plan)
        path.chmod(0o644); path.write_bytes(raw); path.chmod(0o444)
        (self.source / 'extra.py').write_text('foreign source\n')
        with self.assertRaises(ResourceBusy):
            recovery.verify_original_source(self.plan)

    def test_missing_closure_pin_or_changed_executable_flags_are_refused(self):
        bad = copy.deepcopy(self.plan); del bad['controller_pins']['resource_broker.py']
        with self.assertRaisesRegex(ResourceBusy, 'COMPLETE_ORIGINAL_CONTROLLER'):
            recovery.verify_original_source(bad)
        (self.source / 'scripts/resource_broker.py').chmod(0o555)
        with self.assertRaisesRegex(ResourceBusy, 'PUBLISHED_0482_CONTROLLER_CHANGED'):
            recovery.verify_original_source(self.plan)

    def test_critical_driver_and_source_symlink_do_not_acquire_profile(self):
        bad = copy.deepcopy(self.plan)
        bad['controller_pins']['word_restricted_pdf.py']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ResourceBusy, 'CONTROLLER_PIN_CHANGED'):
            recovery.verify_original_source(bad)
        alias = self.source.with_name('source-alias'); alias.symlink_to(self.source, target_is_directory=True)
        bad = {**self.plan, 'controller_source': str(alias)}
        with self.assertRaisesRegex(ResourceBusy, 'SUPPORTED_ORIGINAL_PREINPUT_CONTROLLER'):
            recovery.verify_original_source(bad)


if __name__ == '__main__':
    unittest.main()
