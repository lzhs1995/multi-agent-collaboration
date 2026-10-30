"""Exact original .73 AX failure closeout, independent negative controls."""
import copy
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
import test_word_preinput_recovery as existing

ORIGINAL_SCRIPT = (Path(__file__).resolve().parent /
                   'fixtures/word-preinput-inventory-v1.applescript').read_text()


class AxReferencePreinputRecoveryTests(existing.PreinputRecoveryTests):
    def setUp(self):
        super().setUp()
        patch.object(recovery, 'verify_original_source', return_value={
            'verified_private_fixture': True, 'preinput_reason': recovery.INVENTORY_REASON,
            'failure_profile': recovery.AX_REFERENCE_PROFILE}).start()
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
        self.script.write_text(ORIGINAL_SCRIPT)
        self.inventory = self.failure.parent / 'PRE-inventory.json'
        self.pre = dict(returncode=1, stdout='', stderr=(str(self.script) +
            ':2817:2837: execution error: ' + recovery.AX_REFERENCE_DIAGNOSTIC + '\n'), apps=self.apps)
        self.inventory.write_text(json.dumps(self.pre))
        self.bindings = self.failure.parent / 'application-bindings.json'
        self.binding = dict(bindings=self.apps, word_launch_requested=False)
        self.bindings.write_text(json.dumps(self.binding))
        self.before = self.broker.get(self.ticket['id'])

    def test_exact_profile_keeps_all_original_pre_bytes_and_result(self):
        value = self.preview()
        refs = value['facts']['native_inventory_evidence']
        original = {name: Path(ref['path']).read_bytes() for name, ref in refs.items()}
        closed = self.r.apply(self.ticket['id'], value['plan_sha256'])
        self.assertEqual(closed['status'], 'FAILED_PREINPUT')
        for name, ref in refs.items():
            self.assertEqual(Path(ref['path']).read_bytes(), original[name])
        self.assertEqual(json.loads(self.broker.get(self.ticket['id'])['result'])['original_result'],
                         self.before['result'])
        self.assertFalse(closed['ordinary_word_release'])

    def test_other_reference_application_position_error_or_partial_result_keeps_pending(self):
        bad = [('returncode', 0), ('returncode', True), ('stdout', 'possible partial inventory'),
               ('stderr', self.pre['stderr'].replace('(-1728)', '(-1712)')),
               ('stderr', self.pre['stderr'].replace('2817:2837', '2818:2838')),
               ('stderr', self.pre['stderr'].replace('文件1  -  已復原', 'unrelated window')),
               ('stderr', self.pre['stderr'].replace('Microsoft Word', 'Zotero')),
               ('stderr', self.pre['stderr'].replace(str(self.script), '/private/other.applescript')),
               ('stderr', self.pre['stderr'] + 'another stage\n')]
        for key, value in bad:
            self.inventory.write_text(json.dumps({**self.pre, key: value}))
            with self.subTest(key=key, value=value), self.assertRaises(ResourceBusy):
                self.preview()
            self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_0472_or_0470_source_profile_cannot_close_the_0473_reference_failure(self):
        old = {'preinput_reason': recovery.INVENTORY_REASON,
               'failure_profile': recovery.WORD_TIMEOUT_PROFILE}
        for source in (old, {'verified_private_fixture': True}):
            with self.subTest(source=source), patch.object(recovery, 'verify_original_source', return_value=source):
                with self.assertRaises(ResourceBusy):
                    self.preview()
            self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_launch_changed_generation_boolean_pid_or_partial_apps_keep_pending(self):
        changed = copy.deepcopy(self.binding)
        changed['bindings']['Microsoft Word']['birth'][1] += 1
        boolean = copy.deepcopy(self.apps)
        boolean['Microsoft Word']['pid'] = True
        partial = {'Microsoft Word': self.apps['Microsoft Word']}
        cases = [({**self.binding, 'word_launch_requested': True}, self.pre),
                 (changed, self.pre),
                 ({**self.binding, 'bindings': boolean}, {**self.pre, 'apps': boolean}),
                 ({**self.binding, 'bindings': partial}, {**self.pre, 'apps': partial})]
        for binding, pre in cases:
            self.bindings.write_text(json.dumps(binding))
            self.inventory.write_text(json.dumps(pre))
            with self.subTest(binding=binding), self.assertRaises(ResourceBusy):
                self.preview()
            self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_changed_script_or_added_native_stage_keeps_pending(self):
        self.script.write_text(ORIGINAL_SCRIPT + '\nactivate application "Microsoft Word"\n')
        with self.assertRaisesRegex(ResourceBusy, 'READ_ONLY_INVENTORY_SCRIPT'):
            self.preview()
        self.script.write_text(ORIGINAL_SCRIPT)
        (self.failure.parent / 'native-stage.json').write_text('{}')
        with self.assertRaisesRegex(ResourceBusy, 'APPLICATION_EVIDENCE_PRESERVED'):
            self.preview()
        self.assertEqual(self.broker.get(self.ticket['id']), self.before)

    def test_pre_evidence_change_after_preview_refuses_exact_sha_apply(self):
        value = self.preview()
        self.inventory.write_text(json.dumps({**self.pre, 'stderr': self.pre['stderr'].rstrip('\n')}))
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


class PublishedAxSourceTests(unittest.TestCase):
    def test_complete_content_modes_members_and_each_original_pin_are_required(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name).resolve()
        source = home / 'original-private-0473/source'
        current = Path(protocol.__file__).resolve().parents[1]
        shutil.copytree(current, source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        # Model the legacy AX revision driver, distinct from the current disk
        # driver. The complete synthetic tree and every pin are measured below.
        revision = source / 'scripts/word_restricted_revision.py'
        revision.write_bytes(b'# Synthetic original AX controller fixture.\n' + revision.read_bytes())
        files = {}
        for path in sorted(source.rglob('*')):
            if path.is_file():
                ref = protocol.pin(path)
                files[path.relative_to(source).as_posix()] = dict(bytes=ref['bytes'], sha256=ref['sha256'],
                                                                 executable=os.access(path, os.X_OK))
                path.chmod(0o555 if files[path.relative_to(source).as_posix()]['executable'] else 0o444)
        plan = dict(controller_source=str(source), controller_pins={
            p.name: protocol.pin(p) for p in (source / 'scripts').glob('*.py')})
        drivers = {name: plan['controller_pins'][name]['sha256'] for name in recovery.AX_DRIVERS}
        with patch.object(Path, 'home', return_value=home), \
                patch.object(recovery, 'AX_SOURCE_COUNT', len(files)), \
                patch.object(recovery, 'AX_SOURCE_BYTES', sum(v['bytes'] for v in files.values())), \
                patch.object(recovery, 'AX_SOURCE_DIGEST', protocol.digest(files)), \
                patch.object(recovery, 'AX_DRIVERS', drivers):
            value = recovery.verify_original_source(plan)
            self.assertEqual(value['failure_profile'], recovery.AX_REFERENCE_PROFILE)
            self.assertEqual(value['members'], len(files))
            for kind in ('wrong_hash', 'missing', 'wrong_path', 'non_object_pins', 'non_object_inventory_pin'):
                bad = copy.deepcopy(plan)
                if kind == 'wrong_hash':
                    bad['controller_pins']['resource_broker.py']['sha256'] = '0' * 64
                elif kind == 'missing':
                    bad['controller_pins'].pop('resource_broker.py')
                elif kind == 'wrong_path':
                    bad['controller_pins']['resource_broker.py']['path'] = '/private/other.py'
                elif kind == 'non_object_pins':
                    bad['controller_pins'] = []
                else:
                    bad['controller_pins']['word_restricted_inventory.py'] = 'unknown pin'
                with self.subTest(kind=kind), self.assertRaises(ResourceBusy):
                    recovery.verify_original_source(bad)
            extra = source / 'unapproved.txt'
            extra.write_text('foreign source member')
            with self.assertRaisesRegex(ResourceBusy, 'COMPLETE_PUBLISHED_0473_CONTROLLER'):
                recovery.verify_original_source(plan)
            extra.unlink()
            target = source / 'scripts/resource_broker.py'
            before = target.read_bytes()
            target.chmod(0o644)
            target.write_bytes(bytes([before[0] ^ 1]) + before[1:])
            target.chmod(0o555 if files['scripts/resource_broker.py']['executable'] else 0o444)
            with self.assertRaisesRegex(ResourceBusy, 'PUBLISHED_0473_CONTROLLER_CHANGED'):
                recovery.verify_original_source(plan)
            target.chmod(0o644)
            target.write_bytes(before)
            target.chmod(0o444 if files['scripts/resource_broker.py']['executable'] else 0o555)
            with self.assertRaises(ResourceBusy):
                recovery.verify_original_source(plan)


if __name__ == '__main__':
    unittest.main()
