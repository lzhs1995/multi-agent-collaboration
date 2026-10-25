"""Bundle/process controls; all process queries are isolated mocks."""
import copy
import os
from pathlib import Path
import plistlib
import tempfile
import types
import unittest
from unittest.mock import patch

import cmux_daemon_identity as identity
import word_restricted_pdf as pdf
import word_restricted_revision as revision
import test_word_restricted_revision as owned_tests


class ApplicationBindingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.bundles, self.facts, self.images = {}, {}, {}
        for name, executable, bundle_id, pid in (
                ('Zotero', 'zotero', 'org.zotero.zotero', 30945),
                ('Microsoft Word', 'Microsoft Word', 'com.microsoft.Word', 10007)):
            bundle = self.root / (name + '.app')
            (bundle / 'Contents/MacOS').mkdir(parents=True)
            info = bundle / 'Contents/Info.plist'
            info.write_bytes(plistlib.dumps(dict(CFBundleIdentifier=bundle_id, CFBundleExecutable=executable)))
            image = bundle / 'Contents/MacOS' / executable
            image.write_bytes(b'private kernel executable fixture')
            image.chmod(0o755)
            s = image.stat()
            self.bundles[name] = (str(bundle), bundle_id)
            self.images[name] = image
            self.facts[pid] = dict(pid=pid, birth=[1792147405, 123], executable=str(image),
                                  file_identity=[s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns])
        self.addCleanup(patch.stopall)
        patch.object(pdf, 'APPLICATION_BUNDLES', self.bundles).start()
        self.process = patch.object(identity, 'process', side_effect=lambda pid: copy.deepcopy(self.facts[pid])).start()
        self.query = patch.object(pdf.subprocess, 'run', side_effect=self.pgrep).start()

    def pgrep(self, argv, **kwargs):
        self.assertEqual(argv[:2], ['/usr/bin/pgrep', '-x'])
        pid = {'zotero': 30945, 'Microsoft Word': 10007}[argv[2]]
        return types.SimpleNamespace(returncode=0, stdout=str(pid) + '\n', stderr='')

    def test_lowercase_bundle_executable_binds_original_zotero(self):
        self.assertEqual(pdf.app_binding('Zotero'), self.facts[30945])
        self.assertEqual([c.args[0][2] for c in self.query.call_args_list], ['zotero', 'zotero'])

    def test_both_application_bindings_keep_public_identity_fields(self):
        values = pdf.app_bindings()
        self.assertEqual(values, {'Zotero': self.facts[30945], 'Microsoft Word': self.facts[10007]})
        self.assertTrue(all(set(x) == set(pdf.APPLICATION_FACTS) for x in values.values()))

    def test_absence_is_optional_and_needs_two_clean_observations(self):
        self.query.side_effect = None
        self.query.return_value = types.SimpleNamespace(returncode=1, stdout='', stderr='')
        self.assertIsNone(pdf.app_binding('Microsoft Word', allow_absent=True))
        self.assertEqual(self.query.call_count, 2)
        with self.assertRaisesRegex(RuntimeError, 'RUNNING_ORIGINAL_APPLICATION_REQUIRED'):
            pdf.app_binding('Zotero')

    def test_ambiguous_invalid_and_failed_process_queries_are_refused(self):
        self.query.side_effect = None
        for rc, output, error in [(0, '30945\n30946\n', ''), (0, 'bad', ''), (0, '0', ''),
                                  (1, '', 'unavailable'), (2, '', 'query failed')]:
            self.query.return_value = types.SimpleNamespace(returncode=rc, stdout=output, stderr=error)
            with self.subTest(rc=rc, output=output), self.assertRaises(RuntimeError):
                pdf.app_binding('Zotero', allow_absent=True)

    def test_kernel_executable_and_file_identity_must_match_bundle(self):
        original = copy.deepcopy(self.facts[30945])
        for key, value in [('executable', '/tmp/foreign-zotero'), ('file_identity', [0, 0, 0, 0]), ('pid', 30946)]:
            self.facts[30945] = {**original, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'KERNEL_EXECUTABLE_MISMATCH'):
                pdf.app_binding('Zotero')

    def test_pid_birth_or_uniqueness_change_is_refused(self):
        original = copy.deepcopy(self.facts[30945])
        self.process.side_effect = [original, {**original, 'birth': [1792147406, 0]}]
        with self.assertRaisesRegex(RuntimeError, 'PID_OR_BUNDLE_CHANGED'):
            pdf.app_binding('Zotero')
        self.process.side_effect = [original, original]
        self.query.side_effect = [types.SimpleNamespace(returncode=0, stdout='30945', stderr=''),
                                  types.SimpleNamespace(returncode=1, stdout='', stderr='')]
        with self.assertRaisesRegex(RuntimeError, 'PID_OR_BUNDLE_CHANGED'):
            pdf.app_binding('Zotero')

    def test_bundle_identifier_or_executable_injection_is_refused(self):
        info = self.root / 'Zotero.app/Contents/Info.plist'
        for bundle_id, executable in [('foreign.zotero', 'zotero'), ('org.zotero.zotero', '../zotero'),
                                      ('org.zotero.zotero', ''), ('org.zotero.zotero', 'dir\\zotero')]:
            info.write_bytes(plistlib.dumps(dict(CFBundleIdentifier=bundle_id, CFBundleExecutable=executable)))
            with self.subTest(executable=executable), self.assertRaisesRegex(RuntimeError, 'EXACT_APPLICATION_EXECUTABLE'):
                pdf.app_binding('Zotero')
        self.assertEqual(self.query.call_count, 0)

    def test_bundle_file_change_after_absence_or_binding_is_refused(self):
        def absent_after_change(argv, **kwargs):
            self.images['Microsoft Word'].write_bytes(b'changed during absence query')
            return types.SimpleNamespace(returncode=1, stdout='', stderr='')
        self.query.side_effect = absent_after_change
        with self.assertRaisesRegex(RuntimeError, 'PID_OR_BUNDLE_CHANGED'):
            pdf.app_binding('Microsoft Word', allow_absent=True)


class RevisionBootstrapBindingTests(unittest.TestCase):
    def test_bootstrap_and_post_start_share_exact_zotero_binding(self):
        fixture = owned_tests.OwnedRevisionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        job, ref = fixture.job()
        plan, plan_ref = fixture.plan(job, ref)
        plan['start_word_if_absent'] = True
        driver = fixture.driver(plan, plan_ref)
        driver.root.mkdir()
        zotero = dict(pid=30945, birth=[1, 2], executable='/private/zotero', file_identity=[1, 2, 3, 4])
        apps = {'Zotero': zotero, 'Microsoft Word': {'pid': 10007}}
        with patch.object(driver, '_word_pid', side_effect=[None, 10007]), \
                patch.object(pdf, 'app_binding', return_value=zotero) as binding, \
                patch.object(pdf, 'app_bindings', return_value=apps), \
                patch.object(revision.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='', stderr='')) as launch:
            driver.bind_apps()
        binding.assert_called_once_with('Zotero', timeout=5)
        self.assertEqual(launch.call_args.args[0], revision.WORD_LAUNCH)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(driver.base.apps['Zotero'], zotero)


if __name__ == '__main__':
    unittest.main()
