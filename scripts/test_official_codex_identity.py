"""Official Codex release names must retain identity and time-budget gates."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import cmux_daemon_identity as identity
import cmux_native_delivery as native
import official_signature_cache as cache


class OfficialCodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.binary = self.root / identity.OFFICIAL_CODEX_NAMES[0]
        self.binary.write_bytes(b'fixture')
        self.binary.chmod(0o700)
        self.launcher = self.root / 'launcher'
        self.launcher.symlink_to(self.binary)
        self.launcher_patch = patch.object(identity, 'OFFICIAL_CODEX_LAUNCHER', self.launcher)
        self.launcher_patch.start()
        self.addCleanup(self.launcher_patch.stop)
        self.verifier = patch.object(identity.subprocess, 'run',
                                     return_value=SimpleNamespace(returncode=0, stdout='', stderr=''))
        self.run = self.verifier.start()
        self.addCleanup(self.verifier.stop)
        self.cache = self.root / 'private-cache'
        self.cache.mkdir(mode=0o700)
        self.cache_patch = patch.object(cache, 'CACHE_DIR', str(self.cache))
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.assertTrue(cache.refresh(self.binary, self.launcher, str(self.cache))['published'])
        self.cache_file = Path(cache.cache_file(str(self.cache), self.binary))
        self.run.reset_mock()

    def file_identity(self):
        s = self.binary.stat()
        return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]

    def accept(self):
        return identity.official_codex(str(self.binary), [str(self.launcher)], self.file_identity())

    def test_installed_signed_release_and_original_codex_name(self):
        self.assertTrue(self.accept())
        self.assertTrue(identity.official_codex('/legacy/codex'))
        self.assertEqual(native._provider({'executable': str(self.binary),
            'argv': [str(self.launcher)], 'file_identity': self.file_identity()}), 'codex')

    def test_signature_verification_uses_shared_budget(self):
        with patch.object(identity.budget, 'timeout', return_value=0.042):
            self.assertTrue(cache.refresh(self.binary, self.launcher, str(self.cache))['published'])
        self.assertEqual(self.run.call_args.kwargs['timeout'], 0.042)
        self.assertIn('-R=' + identity.OFFICIAL_CODEX_REQUIREMENT, self.run.call_args.args[0])

    def test_hot_admission_uses_protected_cache_and_refuses_changed_fresh_file_pin(self):
        self.assertTrue(self.accept())
        self.assertTrue(self.accept())
        self.assertEqual(self.run.call_count, 0)
        before = self.binary.stat()
        self.binary.write_bytes(b'changed')
        os.utime(self.binary, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.run.return_value.returncode = 1
        self.assertFalse(self.accept())
        self.assertEqual(self.run.call_count, 0)

    def test_unsigned_or_signature_failure_refused(self):
        self.cache_file.unlink()
        self.run.return_value.returncode = 1
        self.assertFalse(cache.refresh(self.binary, self.launcher, str(self.cache))['published'])
        self.assertFalse(self.accept())

    def test_timeout_refused(self):
        self.cache_file.unlink()
        self.run.side_effect = subprocess.TimeoutExpired('codesign', 0.01)
        self.assertFalse(cache.refresh(self.binary, self.launcher, str(self.cache))['published'])
        self.assertFalse(self.accept())

    def test_expired_budget_refused_before_verification(self):
        with patch.object(identity.budget, 'check', side_effect=subprocess.TimeoutExpired('budget', 0)):
            self.assertFalse(self.accept())
        self.run.assert_not_called()

    def test_wrong_launcher_samefile_refused(self):
        other = self.root / 'other'
        other.write_bytes(b'other')
        self.launcher.unlink()
        self.launcher.symlink_to(other)
        self.assertFalse(self.accept())
        self.run.assert_not_called()

    def test_wrong_or_relative_argv_refused(self):
        for argv in ([str(self.root / 'other')], [self.binary.name], []):
            with self.subTest(argv=argv):
                self.assertFalse(identity.official_codex(str(self.binary), argv))
        self.run.assert_not_called()

    def test_stale_file_identity_refused(self):
        stale = self.file_identity()
        stale[2] += 1
        self.assertFalse(identity.official_codex(str(self.binary), [str(self.binary)], stale))
        self.run.assert_not_called()

    def test_writable_binary_refused(self):
        self.binary.chmod(0o722)
        self.assertFalse(self.accept())
        self.run.assert_not_called()

    def test_relative_and_renamed_binary_refused(self):
        self.assertFalse(identity.official_codex(self.binary.name))
        self.assertFalse(identity.official_codex(str(self.root / 'fake-codex')))
        self.run.assert_not_called()

    def test_binary_replacement_during_signature_refused(self):
        digest = cache._digest
        def change(*args, **kwargs):
            value = digest(*args, **kwargs)
            replacement = self.root / 'replacement'
            replacement.write_bytes(b'changed')
            replacement.chmod(0o700)
            replacement.replace(self.binary)
            return value
        with patch.object(cache, '_digest', side_effect=change):
            self.assertFalse(self.accept())

    def test_launcher_retarget_during_signature_refused(self):
        digest = cache._digest
        def change(*args, **kwargs):
            value = digest(*args, **kwargs)
            self.launcher.unlink()
            self.launcher.symlink_to('/bin/sh')
            return value
        with patch.object(cache, '_digest', side_effect=change):
            self.assertFalse(self.accept())

    def test_file_mode_change_during_signature_refused(self):
        digest = cache._digest
        def change(*args, **kwargs):
            value = digest(*args, **kwargs)
            self.binary.chmod(0o500)
            return value
        with patch.object(cache, '_digest', side_effect=change):
            self.assertFalse(self.accept())

    def test_client_candidates_reads_full_native_paths(self):
        listing = SimpleNamespace(stdout=f'123 {self.binary}\n124 /legacy/codex\n125 /bin/sh\n',
                                  returncode=0)
        def dispatch(argv, **kwargs):
            return SimpleNamespace(returncode=0) if argv[0] == '/usr/bin/codesign' else listing
        self.run.side_effect = dispatch
        self.assertEqual(identity.client_candidates(), ['123', '124'])

    def test_client_candidates_rejects_duplicate_and_malformed_inventory(self):
        for stdout in (f'123 {self.binary}\n123 {self.binary}\n', 'not-a-pid /bin/sh\n'):
            with self.subTest(stdout=stdout):
                self.run.side_effect = lambda argv, **kwargs: (
                    SimpleNamespace(returncode=0) if argv[0] == '/usr/bin/codesign'
                    else SimpleNamespace(stdout=stdout, returncode=0))
                with self.assertRaises(identity.IdentityError):
                    identity.client_candidates()

    def test_native_target_inventory_includes_official_release_name(self):
        row = dict(tty='ttys123', target_surface_uuid='A0ECA53E-4058-4137-9274-15D326B9DD29',
                   workspace_uuid='6FB20312-9E66-43DB-AF09-92C3B18E1296')
        process = dict(pid=123, birth=[1, 2], executable=str(self.binary),
            argv=[str(self.launcher)], file_identity=self.file_identity(),
            env={'CMUX_SURFACE_ID':row['target_surface_uuid'],
                 'CMUX_WORKSPACE_ID':row['workspace_uuid']})
        listing = SimpleNamespace(stdout=f'123 {os.getuid()} ttys123 {self.binary}\n')
        with patch.object(native, '_process_observation', return_value=listing), \
                patch.object(native.identity, 'process', return_value=process), \
                patch.object(native, '_process_tty', return_value='ttys123'):
            self.assertEqual(native._target_process(row), process)


if __name__ == '__main__':
    unittest.main()
