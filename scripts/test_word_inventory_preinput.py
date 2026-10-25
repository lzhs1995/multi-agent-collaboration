"""Read-only PRE timeout closeout and published-controller boundary controls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from resource_broker import ResourceBusy
import word_preinput_recovery as recovery
import word_restricted_broker as protocol
# Historical PRE evidence must retain its original script when CURRENT changes.
SCRIPT = (Path(__file__).resolve().parent / 'fixtures/word-preinput-inventory-v1.applescript').read_text()
import test_word_preinput_recovery as existing


class InventoryPreinputRecoveryTests(existing.PreinputRecoveryTests):
    def setUp(self):
        super().setUp()
        patch.object(recovery, 'verify_original_source', return_value={
            'verified_private_fixture': True, 'preinput_reason': recovery.INVENTORY_REASON}).start()
        error = json.loads(self.original_result['stderr'])
        error['reason'] = recovery.INVENTORY_REASON
        self.original_result['stderr'] = json.dumps(error)
        self.broker.db.execute('UPDATE word_restricted_tickets SET result=? WHERE id=?',
                              (json.dumps(self.original_result), self.ticket['id']))
        failure = json.loads(self.failure.read_text())
        failure['reason'] = recovery.INVENTORY_REASON
        self.failure.write_text(json.dumps(failure))
        self.apps = {
            'Microsoft Word': dict(pid=100000910, birth=[1, 2],
                executable='/Applications/Microsoft Word.app/Contents/MacOS/Microsoft Word',
                file_identity=[1, 2, 3, 4]),
            'Zotero': dict(pid=100000920, birth=[5, 6],
                executable='/Applications/Zotero.app/Contents/MacOS/zotero',
                file_identity=[5, 6, 7, 8]),
        }
        self.script = self.failure.parent / 'pre.applescript'
        self.script.write_text(SCRIPT)
        self.inventory = self.failure.parent / 'PRE-inventory.json'
        self.pre = dict(returncode=1, stdout='', stderr=(str(self.script) +
            ':994:1009: execution error: Microsoft Word got an error: AppleEvent timed out. (-1712)\n'),
            apps=self.apps)
        self.inventory.write_text(json.dumps(self.pre))
        self.bindings = self.failure.parent / 'application-bindings.json'
        self.binding = dict(bindings=self.apps, word_launch_requested=False)
        self.bindings.write_text(json.dumps(self.binding))
        self.before = self.broker.get(self.ticket['id'])

    def test_readonly_inventory_proof_is_retained_in_the_closed_result(self):
        value = self.preview()
        refs = value['facts']['native_inventory_evidence']
        self.assertEqual(set(refs), {'pre.applescript', 'PRE-inventory.json', 'application-bindings.json'})
        original = {name: Path(ref['path']).read_bytes() for name, ref in refs.items()}
        closed = self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(closed['status'], 'FAILED_PREINPUT')
        self.assertEqual(closed['closeout']['evidence']['native_inventory_evidence'], refs)
        for name, ref in refs.items():
            self.assertEqual(Path(ref['path']).read_bytes(), original[name])
        self.assertEqual(json.loads(self.broker.get(self.ticket['id'])['result'])['original_result'],
                         self.before['result'])

    def test_success_partial_output_other_error_or_foreign_script_is_not_a_timeout_closeout(self):
        changes = [('returncode', 0), ('returncode', True), ('stdout', 'possible document metadata'),
                   ('stderr', self.pre['stderr'].replace('(-1712)', '(-1743)')),
                   ('stderr', self.pre['stderr'].replace(str(self.script), '/private/other.applescript')),
                   ('stderr', self.pre['stderr'] + 'unconfirmed additional stage\n')]
        for key, value in changes:
            pre = {**self.pre, key: value}
            self.inventory.write_text(json.dumps(pre))
            with self.subTest(key=key, value=value), self.assertRaises(ResourceBusy):
                self.preview()
            self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_recorded_launch_or_different_application_generation_keeps_pending(self):
        launched = {**self.binding, 'word_launch_requested': True}
        changed = copy.deepcopy(self.binding)
        changed['bindings']['Microsoft Word']['birth'][1] += 1
        for binding in (launched, changed):
            self.bindings.write_text(json.dumps(binding))
            with self.subTest(binding=binding), self.assertRaises(ResourceBusy):
                self.preview()
            self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_missing_application_and_boolean_pid_keep_pending(self):
        bad = copy.deepcopy(self.apps)
        bad['Microsoft Word']['pid'] = True
        for apps in ({'Microsoft Word': self.apps['Microsoft Word']}, bad):
            self.inventory.write_text(json.dumps({**self.pre, 'apps': apps}))
            self.bindings.write_text(json.dumps({**self.binding, 'bindings': apps}))
            with self.subTest(apps=apps), self.assertRaises(ResourceBusy):
                self.preview()
            self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_changed_original_script_and_symlinked_proof_keep_pending(self):
        self.script.write_text(SCRIPT + '\nactivate application "Microsoft Word"\n')
        with self.assertRaisesRegex(ResourceBusy, 'READ_ONLY_INVENTORY_SCRIPT'):
            self.preview()
        self.script.write_text(SCRIPT)
        displaced = self.inventory.with_name('saved-inventory.json')
        self.inventory.rename(displaced)
        self.inventory.symlink_to(displaced)
        with self.assertRaises(ResourceBusy):
            self.preview()
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_old_binding_failure_profile_cannot_close_new_inventory_reason(self):
        with patch.object(recovery, 'verify_original_source', return_value={'verified_private_fixture': True}):
            with self.assertRaisesRegex(ResourceBusy, 'SUPPORTED_PREINPUT_FAILURE'):
                self.preview()
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_modified_inventory_between_preview_and_apply_keeps_pending(self):
        value = self.preview()
        pre = {**self.pre, 'stderr': self.pre['stderr'].replace(':994:1009:', ':995:1010:')}
        self.inventory.write_text(json.dumps(pre))
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_PLAN'):
            self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_stale_hash_and_replaced_evidence_directory_are_refused(self):
        value = self.preview()
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_PLAN'):
            self.r.apply(self.ticket['id'], '0' * 64)
        parent = self.failure.parent
        displaced = parent.with_name('old-evidence')
        parent.rename(displaced)
        parent.mkdir()
        for path in displaced.iterdir():
            (parent / path.name).write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ResourceBusy, 'EXACT_PREINPUT_PLAN'):
            self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)


class PublishedInventorySourceTests(unittest.TestCase):
    def test_full_private_copy_paths_members_modes_and_pins_are_bound(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name).resolve()
        source = home / 'private-original-controller/source'
        current = Path(protocol.__file__).resolve().parents[1]
        shutil.copytree(current, source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        files = {}
        for path in sorted(source.rglob('*')):
            if path.is_file():
                ref = protocol.pin(path)
                name = path.relative_to(source).as_posix()
                files[name] = dict(bytes=ref['bytes'], sha256=ref['sha256'], executable=os.access(path, os.X_OK))
                path.chmod(0o555 if files[name]['executable'] else 0o444)
        plan = dict(controller_source=str(source), controller_pins={
            p.name: protocol.pin(p) for p in (source / 'scripts').glob('*.py')})
        critical = {name: plan['controller_pins'][name]['sha256'] for name in recovery.INVENTORY_DRIVERS}
        with patch.object(Path, 'home', return_value=home), \
                patch.object(recovery, 'INVENTORY_SOURCE_COUNT', len(files)), \
                patch.object(recovery, 'INVENTORY_SOURCE_BYTES', sum(ref['bytes'] for ref in files.values())), \
                patch.object(recovery, 'INVENTORY_SOURCE_DIGEST', protocol.digest(files)), \
                patch.object(recovery, 'INVENTORY_DRIVERS', critical):
            value = recovery.verify_original_source(plan)
            self.assertEqual(value['members'], len(files))
            self.assertEqual(value['source'], str(source))
            self.assertEqual(value['preinput_reason'], recovery.INVENTORY_REASON)
            bad = copy.deepcopy(plan)
            bad['controller_pins']['resource_broker.py']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ResourceBusy, 'CONTROLLER_PIN_CHANGED'):
                recovery.verify_original_source(bad)
            bad = copy.deepcopy(plan)
            bad['controller_pins'].pop('resource_broker.py')
            with self.assertRaisesRegex(ResourceBusy, 'COMPLETE_ORIGINAL_CONTROLLER'):
                recovery.verify_original_source(bad)
            extra = source / 'unreviewed-controller-note.txt'
            extra.write_text('not in the published original')
            with self.assertRaisesRegex(ResourceBusy, 'COMPLETE_PUBLISHED_0472_CONTROLLER'):
                recovery.verify_original_source(plan)
            extra.unlink()
            changed = source / 'scripts/resource_broker.py'
            original = changed.read_bytes()
            changed.chmod(0o644)
            changed.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            changed.chmod(0o555 if files['scripts/resource_broker.py']['executable'] else 0o444)
            with self.assertRaisesRegex(ResourceBusy, 'PUBLISHED_0472_CONTROLLER_CHANGED'):
                recovery.verify_original_source(plan)
            changed.chmod(0o644)
            changed.write_bytes(original)
            changed.chmod(0o444 if files['scripts/resource_broker.py']['executable'] else 0o555)
            with self.assertRaises(ResourceBusy):
                recovery.verify_original_source(plan)


if __name__ == '__main__':
    unittest.main()
