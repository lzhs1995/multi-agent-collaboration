"""Merged signature-cache positives, fail-closed cases and shared deadlines."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import official_signature_cache as cache
import cmux_daemon_identity as identity


class SignatureCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.binary = self.root / identity.OFFICIAL_CODEX_NAMES[0]
        self.binary.write_bytes(b"signed-binary-fixture")
        self.binary.chmod(0o700)
        self.launcher = self.root / "codex"
        self.launcher.symlink_to(self.binary)
        self.directory = self.root / "cache"
        self.directory.mkdir(mode=0o700)
        patch = mock.patch.object(cache, "CACHE_DIR", str(self.directory))
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(identity, "OFFICIAL_CODEX_LAUNCHER", self.launcher)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(cache.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.runner = patch.start()
        self.addCleanup(patch.stop)
        self.assertTrue(cache.refresh(self.binary, self.launcher, str(self.directory))["published"])
        self.path = Path(cache.cache_file(str(self.directory), self.binary))
        self.original_cache = self.path.read_bytes()
        self.runner.reset_mock()

    def check(self):
        return cache.check(self.binary, self.launcher)

    def edit_record(self, **delta):
        value = json.loads(self.path.read_bytes())
        value.update(delta)
        self.path.write_text(json.dumps(value))

    def test_merged_hot_route_authenticates_without_codesign(self):
        self.assertEqual(self.check(), (True, None))
        self.assertTrue(identity.official_codex(str(self.binary), [str(self.launcher)]))
        self.runner.assert_not_called()

    def test_missing_cache_never_falls_back_to_slow_verification(self):
        self.path.unlink()
        self.assertFalse(identity.official_codex(str(self.binary), [str(self.launcher)]))
        self.runner.assert_not_called()

    def test_bad_cache_schema_signature_and_digest_are_refused(self):
        for delta in ({"schema": "other"}, {"requirement": "other"}, {"codesign_rc": False},
                      {"codesign_rc": 1}, {"sha256": "0" * 64}, {"uid": -1}):
            with self.subTest(delta=delta):
                self.path.write_bytes(self.original_cache)
                self.edit_record(**delta)
                ok, reason = self.check()
                self.assertFalse(ok)
                self.assertTrue(reason.startswith("SIGNATURE_REFRESH_REQUIRED"))
        self.runner.assert_not_called()

    def test_private_directory_and_cache_permissions_are_required(self):
        for mode in (0o644, 0o700, 0o666):
            with self.subTest(mode=mode):
                self.path.chmod(mode)
                self.assertFalse(self.check()[0])
        self.path.chmod(0o600)
        self.directory.chmod(0o755)
        self.assertFalse(self.check()[0])

    def test_symlink_hardlink_and_special_cache_files_are_refused(self):
        other = self.root / "other-cache"
        self.path.rename(other)
        self.path.symlink_to(other)
        self.assertFalse(self.check()[0])
        self.path.unlink()
        os.link(other, self.path)
        self.assertFalse(self.check()[0])
        self.path.unlink()
        os.mkfifo(self.path, mode=0o600)
        self.assertFalse(self.check()[0])

    def test_duplicate_fields_and_oversized_cache_are_refused(self):
        self.path.write_bytes(b'{"schema":"first","schema":"second"}')
        self.assertFalse(self.check()[0])
        self.path.write_bytes(b" " * (cache.MAX_BYTES + 1))
        self.assertFalse(self.check()[0])

    def test_launcher_drift_during_digest_is_refused_on_first_call(self):
        digest = cache._digest
        other = self.root / "other-binary"
        other.write_bytes(b"other")
        def move(path):
            value = digest(path)
            self.launcher.unlink()
            self.launcher.symlink_to(other)
            return value
        with mock.patch.object(cache, "_digest", side_effect=move):
            self.assertFalse(identity.official_codex(str(self.binary), [str(self.launcher)]))
        self.runner.assert_not_called()

    def test_content_change_with_preserved_mtime_is_refused(self):
        before = self.binary.stat()
        self.binary.write_bytes(b"changed-binary-fixture")
        os.utime(self.binary, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertFalse(self.check()[0])
        self.runner.assert_not_called()

    def test_expired_entry_publishes_nothing_and_invokes_no_codesign(self):
        token = cache.budget._deadline.set(time.monotonic() - 1)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                cache.refresh(self.binary, self.launcher, str(self.directory))
        finally:
            cache.budget._deadline.reset(token)
        self.runner.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original_cache)

    def test_expiry_during_fsync_never_replaces_the_published_cache(self):
        fsync = os.fsync
        replace = os.replace
        def expire(fd):
            fsync(fd)
            cache.budget._deadline.set(time.monotonic() - 1)
        with mock.patch.object(cache.os, "fsync", side_effect=expire), \
             mock.patch.object(cache.os, "replace", wraps=replace) as published:
            with self.assertRaises(subprocess.TimeoutExpired):
                with cache.budget.limit(1):
                    cache.refresh(self.binary, self.launcher, str(self.directory))
            published.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original_cache)

    def test_refresh_uses_shared_remaining_subprocess_budget(self):
        with mock.patch.object(cache.budget, "timeout", return_value=0.125):
            self.assertTrue(cache.refresh(self.binary, self.launcher, str(self.directory), timeout=30)["published"])
        self.assertEqual(self.runner.call_args.kwargs["timeout"], 0.125)

    def test_explicit_codesign_failure_and_local_timeout_do_not_publish(self):
        self.runner.return_value.returncode = 1
        self.assertFalse(cache.refresh(self.binary, self.launcher, str(self.directory))["published"])
        self.runner.side_effect = subprocess.TimeoutExpired("codesign", 0.1)
        self.assertFalse(cache.refresh(self.binary, self.launcher, str(self.directory))["published"])
        self.assertEqual(self.path.read_bytes(), self.original_cache)

    def test_status_cli_has_finite_budget_and_never_runs_codesign(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = cache.main(["status", "--cache-dir", str(self.directory),
                "--executable", str(self.binary), "--launcher", str(self.launcher), "--budget-seconds", "2.5"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "VERIFIED_CACHE")
        self.runner.assert_not_called()

    def test_status_cli_reports_timeout_without_unbounded_fallback(self):
        output = io.StringIO()
        with mock.patch.object(cache, "check", side_effect=subprocess.TimeoutExpired("budget", 0)), \
             redirect_stdout(output):
            status = cache.main(["status", "--cache-dir", str(self.directory),
                "--executable", str(self.binary), "--launcher", str(self.launcher)])
        self.assertEqual(status, 3)
        self.assertIn("TimeoutExpired", json.loads(output.getvalue())["reason"])
        self.runner.assert_not_called()

    def test_status_cli_rejects_invalid_budget_before_any_read(self):
        for value in ("nan", "inf", "-1", "0", "61"):
            with self.subTest(value=value), mock.patch.object(cache, "check") as checked, redirect_stdout(io.StringIO()):
                self.assertEqual(cache.main(["status", "--cache-dir", str(self.directory),
                    "--executable", str(self.binary), "--launcher", str(self.launcher), "--budget-seconds", value]), 3)
                checked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
