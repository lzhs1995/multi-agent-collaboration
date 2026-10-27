"""Private SQLite/CLI, exact evidence and original-lock closeout controls.

Fixtures simulate recorded Word receipts; no real application is invoked.
The real original-ticket preview is a separate frozen maintenance observation.
"""
import copy
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zipfile

from resource_broker import ResourceBusy, os_lock
import word_postnative_closeout as closeout
import word_restricted_broker as protocol
import word_restricted_inventory as inventory
import word_restricted_revision as revision
import test_word_restricted_revision as owned_tests


class PostnativeCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.f = owned_tests.OwnedRevisionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.ticket, self.plan, self.ref, _contract = self.f.request()
        self.broker = self.f.f.broker
        granted = self.broker.grant(self.ticket['id'])
        nonce, pgid = str(uuid.uuid4()), 100000800
        argv = ['/private/python', '-BE', str(Path(self.plan['controller_source']) / 'scripts/word_restricted_revision.py'),
                '--root', str(self.broker.root), '--ticket', self.ticket['id'],
                '--token', granted['token'], '--nonce', nonce]
        error = dict(status='RESTRICTED_NATIVE_PENDING', reason=closeout.REASON, error_type='ResourceBusy',
                     no_retry=True, no_foreign_save_or_close=True, legacy_released=False)
        result = dict(exit_code=2, stdout='', stderr=json.dumps(error), pgid=pgid,
                      process_group_empty=True, original_argv=argv)
        self.broker.db.execute("UPDATE word_restricted_tickets SET status='PENDING',invocations=1,nonce=?,"
                               'parent_pid=100000700,pgid=?,result=? WHERE id=?',
                               (nonce, pgid, json.dumps(result), self.ticket['id']))
        self.root = Path(self.plan['evidence_root'])
        self.root.mkdir(); (self.root / 'tracked').mkdir()
        self.tracked = revision.owned_path(self.plan, 'tracked')
        self.tracked.parent.mkdir()
        with zipfile.ZipFile(self.f.input) as source, zipfile.ZipFile(self.tracked, 'w') as target:
            for name in source.namelist():
                target.writestr(name, source.read(name).replace(b'Original', b'NativeChanged'))
        self.write('failure.json', dict(reason=closeout.REASON, error_type='ResourceBusy', may_be_open=False,
                   owned_document=str(self.tracked), retry_permitted=False, no_foreign_save_or_close=True))
        self.write('tracked/owned-input.json', dict(source=self.plan['input'], action='tracked',
                   copy={**self.plan['input'], 'path': str(self.tracked)},
                   revision_headers=revision.revision_headers(self.f.input)))
        for stage in revision.STAGES:
            script = self.root / 'tracked' / (stage.lower() + '.applescript')
            script.write_bytes(revision.expected_revision_script(self.plan, 'tracked', stage))
            ref = protocol.pin(script)
            self.write('tracked/' + stage.lower() + '-intent.json',
                       dict(stage=stage, owned=str(self.tracked), script=ref, plan=self.ref, replay_allowed=False))
            self.write('tracked/' + stage.lower() + '-result.json',
                       dict(stage=stage, script=ref, returncode=0, stdout=stage + '\n', stderr='',
                            owned_document=str(self.tracked)))
        self.bindings = {'Microsoft Word': {'pid': 100000500}, 'Zotero': {'pid': 100000600}}
        self.write('application-bindings.json', dict(bindings=self.bindings, word_launch_requested=False))
        (self.root / 'pre.applescript').write_bytes(inventory.SCRIPT.encode())
        pre_text = 'WORD_RESTRICTED_INVENTORY_V1\t0\t0\nACTIVE\tMISSING\nAXAPP\tMicrosoft Word\t0\nAXAPP\tZotero\t0\nEND'
        pre = self.write('PRE-inventory.json', dict(returncode=0, stdout=pre_text, stderr='', apps=self.bindings))
        self.write('native-intent.json', dict(ticket=self.ticket['id'], nonce=nonce, plan=self.ref,
                   input=self.plan['input'], before=inventory.parse(pre_text), pre=pre,
                   operation=protocol.REVISION_SCOPE, source_semantics_verification='PENDING_PARENT_SOURCE_CONTRACT'))
        self.actor = self.f.f.successor
        self.addCleanup(patch.stopall)
        patch.object(closeout, 'verify_source', return_value={'private_fixture_only': True}).start()
        self.group = patch.object(closeout, 'group_alive', return_value=False).start()
        self.cli_sys = SimpleNamespace(flags=SimpleNamespace(ignore_environment=True), dont_write_bytecode=True)
        patch.object(closeout, 'sys', self.cli_sys).start()
        self.c = closeout.PostnativeCloseout(self.broker.root, readonly=False)
        self.addCleanup(self.c.close)
        self.before = self.broker.get(self.ticket['id'])
        self.original_files = self.files()

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False) + '\n')
        return protocol.pin(path)

    def files(self):
        roots = [self.root, self.tracked.parent]
        return {str(path): path.read_bytes() for root in roots for path in root.rglob('*') if path.is_file()}

    def preview(self):
        return self.c.preview(self.ticket['id'])

    def refusal(self, reason=None):
        before = list(self.broker.db.iterdump())
        with self.assertRaisesRegex(ResourceBusy, reason) if reason else self.assertRaises((ResourceBusy, ValueError, OSError)):
            self.preview()
        self.assertEqual(list(self.broker.db.iterdump()), before)

    def cli(self, command, sha=None):
        args = [command, '--root', str(self.broker.root), '--ticket', self.ticket['id']]
        if sha is not None:
            args += ['--plan-sha256', sha]
        output = io.StringIO()
        with redirect_stdout(output):
            rc = closeout.main(args)
        return rc, json.loads(output.getvalue())

    def test_real_cli_preview_changes_no_queue_schema_or_original_bytes(self):
        before = list(self.broker.db.iterdump())
        with patch('subprocess.run', side_effect=AssertionError('no process/input permitted')):
            rc, value = self.cli('preview')
        self.assertEqual(rc, 0)
        self.assertEqual(value['outcome'], 'OWNED_POSTNATIVE_READ_ONLY_PLAN')
        self.assertEqual(value['facts']['original_ticket'], self.before)
        self.assertEqual(value['facts']['headers_before'], 2)
        self.assertEqual(value['facts']['headers_after'], 2)
        self.assertEqual(value['queue_changes'], 0)
        self.assertEqual(value['input_operations'], 0)
        self.assertEqual(list(self.broker.db.iterdump()), before)
        self.assertEqual(self.files(), self.original_files)

    def test_apply_preserves_every_row_field_except_status_and_uses_separate_receipt(self):
        sha = self.preview()['plan_sha256']
        with patch('subprocess.run', side_effect=AssertionError('no process/input permitted')):
            rc, value = self.cli('apply', sha)
        self.assertEqual(rc, 0)
        self.assertEqual(value['status'], 'FAILED_POSTNATIVE')
        after = self.broker.get(self.ticket['id'])
        for key in self.before.keys() - {'status'}:
            self.assertEqual(after[key], self.before[key], key)
        receipt = self.broker.db.execute('SELECT * FROM ' + closeout.TABLE).fetchone()
        self.assertEqual(receipt['original_result_sha256'], hashlib.sha256(self.before['result'].encode()).hexdigest())
        recorded = json.loads(receipt['receipt'])
        self.assertEqual(recorded['original_evidence']['original_ticket']['result'], self.before['result'])
        self.assertEqual(recorded['application_inputs'], 0)
        for key in ('legacy_released', 'ordinary_word_release', 'new_run_authorized',
                    'table_geometry_accepted', 'final_paper_acceptance'):
            self.assertFalse(recorded[key], key)
        self.assertEqual(self.files(), self.original_files)
        self.assertEqual(self.f.f.legacy.get(self.f.f.original['id']), self.f.f.original)

    def test_closeout_is_not_a_run_refund_or_replay(self):
        sha = self.preview()['plan_sha256']
        self.c.apply(self.ticket['id'], sha)
        after = self.broker.get(self.ticket['id'])
        with self.assertRaises(ResourceBusy):
            self.c.apply(self.ticket['id'], sha)
        with self.assertRaises(ResourceBusy):
            self.broker.run(self.ticket['id'], after['token'])
        self.assertEqual(after['invocations'], 1)

    def test_a_separately_approved_later_job_retains_fifo_after_failure_closeout(self):
        self.c.apply(self.ticket['id'], self.preview()['plan_sha256'])
        first, _plan, _ref, _contract = self.f.request('next-one')
        second, _plan, _ref, _contract = self.f.request('next-two')
        with self.assertRaisesRegex(ResourceBusy, 'FIFO'):
            self.broker.grant(second['id'])
        self.assertEqual(self.broker.grant(first['id'])['invocations'], 0)
        self.assertEqual(self.f.f.legacy.get(self.f.f.original['id']), self.f.f.original)

    def test_missing_or_wrong_exact_plan_is_structured_and_zero_write(self):
        before = list(self.broker.db.iterdump())
        for sha in (None, '0' * 64):
            with self.subTest(sha=sha):
                rc, value = self.cli('apply', sha)
                self.assertEqual(rc, 2)
                self.assertEqual(value['reason'], 'EXACT_POSTNATIVE_PLAN_REQUIRED')
                self.assertEqual(value['input_operations'], 0)
        self.assertEqual(list(self.broker.db.iterdump()), before)

    def test_missing_be_flags_refuse_before_queue_open(self):
        for flag in ('ignore_environment', 'dont_write_bytecode'):
            obj = self.cli_sys.flags if flag == 'ignore_environment' else self.cli_sys
            with self.subTest(flag=flag), patch.object(obj, flag, False), patch.object(closeout, 'PostnativeCloseout') as opened:
                rc, value = self.cli('preview')
                self.assertEqual(rc, 2)
                self.assertEqual(value['reason'], 'POSTNATIVE_CLI_REQUIRES_BE')
                opened.assert_not_called()

    def test_wrong_actor_surface_workspace_pane_or_thread_has_zero_changes(self):
        sha = self.preview()['plan_sha256']
        before = list(self.broker.db.iterdump())
        for key in ('surface_uuid', 'workspace_uuid', 'pane_uuid', 'session_id'):
            with self.subTest(key=key):
                self.f.f.identity.return_value = {**self.actor, key: str(uuid.uuid4())}
                with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_POSTNATIVE_ACTOR_REQUIRED'):
                    self.c.apply(self.ticket['id'], sha)
        self.assertEqual(list(self.broker.db.iterdump()), before)

    def test_identity_switch_under_original_locks_is_refused(self):
        sha = self.preview()['plan_sha256']
        with patch.object(protocol, 'native_identity', side_effect=[self.actor, {**self.actor, 'session_id': str(uuid.uuid4())}]):
            with self.assertRaisesRegex(ResourceBusy, 'ORIGINAL_POSTNATIVE_ACTOR_REQUIRED'):
                self.c.apply(self.ticket['id'], sha)
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_alive_or_unknown_original_group_stays_pending(self):
        self.group.return_value = True
        self.refusal('ORIGINAL_CHILD_ALIVE_OR_UNKNOWN')

    def test_changed_original_error_result_or_child_argv_stays_pending(self):
        value = json.loads(self.before['result'])
        changes = [lambda r: r.update(process_group_empty=False), lambda r: r.update(exit_code=0),
                   lambda r: r['original_argv'].__setitem__(2, '/foreign/driver.py'),
                   lambda r: r.update(stderr=json.dumps({'reason': 'UNKNOWN'}))]
        for change in changes:
            candidate = copy.deepcopy(value); change(candidate)
            self.broker.db.execute('UPDATE word_restricted_tickets SET result=? WHERE id=?', (json.dumps(candidate), self.ticket['id']))
            with self.subTest(result=candidate):
                self.refusal()

    def test_preinput_or_open_native_failure_cannot_use_postnative_closeout(self):
        failure = json.loads((self.root / 'failure.json').read_text())
        for change in ({'may_be_open': True}, {'retry_permitted': True}, {'reason': 'APPLEEVENT_TIMEOUT'}):
            self.write('failure.json', {**failure, **change})
            with self.subTest(change=change):
                self.refusal('ORIGINAL_CLOSED_POSTNATIVE_FAILURE_REQUIRED')

    def test_each_of_five_native_results_and_scripts_is_required(self):
        for stage in revision.STAGES:
            for suffix in ('-result.json', '.applescript'):
                path = self.root / 'tracked' / (stage.lower() + suffix)
                raw = path.read_bytes(); path.unlink()
                with self.subTest(stage=stage, suffix=suffix):
                    self.refusal('EXACT_COMPLETED_TRACKED_STAGE_EVIDENCE_REQUIRED')
                path.write_bytes(raw)

    def test_wrong_returncode_or_owned_document_in_each_native_stage_is_refused(self):
        for stage in revision.STAGES:
            name = 'tracked/' + stage.lower() + '-result.json'
            old = json.loads((self.root / name).read_text())
            for change in ({'returncode': 1}, {'returncode': False}, {'owned_document': '/foreign.docx'}):
                self.write(name, {**old, **change})
                with self.subTest(stage=stage, change=change):
                    self.refusal('ALL_FIVE_ORIGINAL_NATIVE_STAGES_REQUIRED')
            self.write(name, old)

    def test_changed_complete_pre_inventory_or_before_metadata_is_refused(self):
        pre = json.loads((self.root / 'PRE-inventory.json').read_text())
        self.write('PRE-inventory.json', {**pre, 'stdout': 'WORD_RESTRICTED_INVENTORY_V1\t0\t0\nEND'})
        self.refusal()

    def test_changed_original_pre_script_is_refused(self):
        (self.root / 'pre.applescript').write_text('return "forged metadata"')
        self.refusal('EXACT_ORIGINAL_COMPLETE_PRE_INVENTORY_REQUIRED')

    def test_no_actual_header_difference_cannot_close_a_different_failure(self):
        self.tracked.write_bytes(self.f.input.read_bytes())
        self.refusal('ACTUAL_ORIGINAL_HEADER_DIFFERENCE_REQUIRED')

    def test_source_or_stage_docx_change_and_malformed_zip_are_structured_refusals(self):
        self.tracked.write_bytes(b'not a docx')
        rc, value = self.cli('preview')
        self.assertEqual(rc, 2)
        self.assertEqual(value['outcome'], 'OWNED_POSTNATIVE_REFUSED')
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_any_artifact_pdf_or_pending_path_prevents_failure_closeout(self):
        paths = [*self.plan['revision_outputs'].values(), self.plan['output'],
                 str(self.f.f.user / '.cache/wordrev/word-automation.pending.json')]
        for value in paths:
            path = Path(value); path.write_bytes(b'preserved')
            with self.subTest(path=value):
                self.refusal('POSTNATIVE_OUTPUT_OR_PENDING_PRESERVED|WORD_PENDING_PRESERVED')
            path.unlink()

    def test_broken_symlink_output_is_also_preserved(self):
        path = Path(self.plan['output']); path.symlink_to('/absent-postnative-output')
        self.refusal('POSTNATIVE_OUTPUT_OR_PENDING_PRESERVED')

    def test_extra_stage_member_is_preserved_and_refused(self):
        path = self.tracked.parent / 'foreign.docx'; path.write_bytes(b'foreign')
        self.refusal('ONLY_ORIGINAL_TRACKED_STAGE_PRESERVED')
        self.assertEqual(path.read_bytes(), b'foreign')

    def test_exact_input_identical_save_backup_is_pinned_and_preserved(self):
        folder = self.tracked.with_suffix(''); folder.mkdir()
        backup = folder / ('備份 ' + self.tracked.name)
        backup.write_bytes(self.f.input.read_bytes())
        value = self.preview()
        pinned = value['facts']['stage_directory']['input_identical_backup']
        self.assertEqual(pinned['file']['sha256'], self.plan['input']['sha256'])
        self.assertEqual(pinned['producer'], 'NOT_INDEPENDENTLY_ATTRIBUTED')
        self.assertFalse(pinned['paper_artifact'])
        before = self.files()
        self.c.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.files(), before)

    def test_changed_extra_or_linked_save_backup_keeps_original_ticket_pending(self):
        folder = self.tracked.with_suffix(''); folder.mkdir()
        backup = folder / ('備份 ' + self.tracked.name)
        backup.write_bytes(b'changed input backup')
        self.refusal('EXACT_INPUT_IDENTICAL_STAGE_BACKUP_REQUIRED')
        backup.write_bytes(self.f.input.read_bytes())
        extra = folder / 'foreign'; extra.write_bytes(b'foreign')
        self.refusal('EXACT_INPUT_IDENTICAL_STAGE_BACKUP_REQUIRED')
        extra.unlink(); backup.unlink(); backup.symlink_to(self.f.input)
        self.refusal()

    def test_backup_change_after_exact_preview_is_not_settled(self):
        folder = self.tracked.with_suffix(''); folder.mkdir()
        backup = folder / ('備份 ' + self.tracked.name); backup.write_bytes(self.f.input.read_bytes())
        value = self.preview(); backup.write_bytes(b'new bytes')
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_INPUT_IDENTICAL_STAGE_BACKUP_REQUIRED'):
            self.c.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_real_original_lock_contention_does_not_mutate_the_queue(self):
        sha = self.preview()['plan_sha256']
        before = list(self.broker.db.iterdump())
        for path in self.plan['locks']:
            with self.subTest(path=path), os_lock(path):
                with self.assertRaisesRegex(ResourceBusy, 'OS_LOCK_BUSY'):
                    self.c.apply(self.ticket['id'], sha)
        self.assertEqual(list(self.broker.db.iterdump()), before)

    def test_disappeared_or_replaced_lock_is_never_created_or_followed(self):
        sha = self.preview()['plan_sha256']
        path = Path(next(iter(self.plan['locks'])))
        path.unlink()
        with self.assertRaises(OSError):
            self.c.apply(self.ticket['id'], sha)
        self.assertFalse(path.exists())
        path.symlink_to(self.tracked)
        with self.assertRaises((ResourceBusy, OSError)):
            self.c.apply(self.ticket['id'], sha)
        self.assertEqual(self.files(), self.original_files)

    def test_evidence_and_output_change_after_preview_rejects_exact_apply(self):
        sha = self.preview()['plan_sha256']
        (self.root / 'tracked/reclose-result.json').write_text('{}')
        with self.assertRaises(ResourceBusy):
            self.c.apply(self.ticket['id'], sha)
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_transaction_preimage_check_catches_late_output_or_row_changes(self):
        sha = self.preview()['plan_sha256']
        original = self.c.broker.transaction
        @contextmanager
        def transaction():
            with original():
                Path(self.plan['output']).write_bytes(b'late artifact')
                yield
        with patch.object(self.c.broker, 'transaction', transaction), self.assertRaises(ResourceBusy):
            self.c.apply(self.ticket['id'], sha)
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)
        self.assertIsNone(self.broker.db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (closeout.TABLE,)).fetchone())

    def test_receipt_insert_failure_rolls_back_the_status_and_schema(self):
        sha = self.preview()['plan_sha256']
        self.broker.db.execute('CREATE TABLE ' + closeout.TABLE + ' (ticket TEXT PRIMARY KEY, '
                               'original_result_sha256 TEXT, receipt TEXT CHECK(receipt IS NULL), closed REAL)')
        before = list(self.broker.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.apply(self.ticket['id'], sha)
        self.assertEqual(list(self.broker.db.iterdump()), before)
        self.assertEqual(self.files(), self.original_files)

    def test_transaction_row_compare_and_swap_refuses_late_metadata_change(self):
        sha = self.preview()['plan_sha256']
        original = self.c.broker.transaction
        @contextmanager
        def transaction():
            with original():
                self.c.broker.db.execute('UPDATE word_restricted_tickets SET parent_pid=parent_pid+1 WHERE id=?',
                                        (self.ticket['id'],))
                yield
        with patch.object(self.c.broker, 'transaction', transaction):
            with self.assertRaisesRegex(ResourceBusy, 'TICKET_COMPARE_AND_SWAP'):
                self.c.apply(self.ticket['id'], sha)
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_symlink_or_special_original_evidence_is_never_opened(self):
        extra = self.root / 'untrusted'
        extra.symlink_to(self.tracked)
        self.refusal('ORIGINAL_EVIDENCE_SYMLINK_REFUSED')
        extra.unlink(); os.mkfifo(extra)
        self.refusal('REGULAR_ORIGINAL_EVIDENCE_REQUIRED')

    def test_directory_scan_stops_before_enumerating_unbounded_entries(self):
        class Scan:
            calls = 0
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def __iter__(self): return self
            def __next__(self):
                self.calls += 1
                if self.calls > closeout.MAX_ENTRIES + 1:
                    raise AssertionError('scanner exceeded finite evidence budget')
                return SimpleNamespace(path=str(self.root / ('file-' + str(self.calls))),
                    stat=lambda **_: SimpleNamespace(st_mode=0o100600, st_size=0))
        scan = Scan(); scan.root = self.root
        with patch.object(closeout.os, 'scandir', return_value=scan), \
                patch.object(closeout, 'bounded_pin', return_value={'path': 'private', 'bytes': 0, 'sha256': '0' * 64}):
            with self.assertRaisesRegex(ResourceBusy, 'BOUNDED_ORIGINAL_POSTNATIVE_EVIDENCE'):
                closeout.evidence_snapshot(self.root)
        self.assertEqual(scan.calls, closeout.MAX_ENTRIES + 1)

    def test_oversized_evidence_and_docx_xml_refuse_before_read_or_decompression(self):
        extra = self.root / 'large.json'
        with extra.open('wb') as stream: stream.truncate(closeout.MAX_TREE_BYTES + 1)
        self.refusal('BOUNDED_ORIGINAL_POSTNATIVE_BYTES')
        extra.unlink()
        with zipfile.ZipFile(self.tracked, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', b'x' * (closeout.MAX_XML_BYTES + 1))
        with self.assertRaisesRegex(ResourceBusy, 'BOUNDED_ORIGINAL_DOCX_XML'):
            self.preview()


if __name__ == '__main__':
    unittest.main()
