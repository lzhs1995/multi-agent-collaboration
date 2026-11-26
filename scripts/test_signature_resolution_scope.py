"""A successful resolution must seal its original full-content signature pin."""
import hashlib
import os
import subprocess
import time
import unittest
from unittest import mock

import official_signature_cache as cache
import test_official_signature_cache as fixtures


class SignatureResolutionScopeTests(unittest.TestCase):
    setUp = fixtures.SignatureCacheTests.setUp
    check = fixtures.SignatureCacheTests.check

    def test_repeated_checks_hash_full_content_at_entry_and_exit(self):
        with mock.patch.object(cache, '_digest', wraps=cache._digest) as digest:
            with cache.validation_scope():
                for _ in range(5):
                    self.assertEqual(self.check(), (True, None))
                self.assertEqual(digest.call_count, 1)
            self.assertEqual(digest.call_count, 2)
            self.assertEqual(self.check(), (True, None))
            self.assertEqual(digest.call_count, 3)

    def test_change_after_final_intermediate_call_cannot_escape_seal(self):
        with self.assertRaisesRegex(ValueError, 'scope changed'):
            with cache.validation_scope():
                self.assertTrue(self.check()[0])
                self.binary.write_bytes(b'changed-binary-fixture')

    def test_exit_hash_detects_bytes_even_with_spoofed_stat_identity(self):
        identity = cache._identity(self.binary.stat())
        with mock.patch.object(cache, '_identity', return_value=identity):
            with self.assertRaisesRegex(ValueError, 'scope changed'):
                with cache.validation_scope():
                    self.assertTrue(self.check()[0])
                    self.binary.write_bytes(b'changed-binary-fixture')

    def test_changed_but_newly_verified_replacement_still_rejects_original_scope(self):
        with self.assertRaisesRegex(ValueError, 'scope changed'):
            with cache.validation_scope():
                self.assertTrue(self.check()[0])
                self.binary.write_bytes(b'changed-binary-fixture')
                self.assertTrue(cache.refresh(self.binary, self.launcher, str(self.directory))['published'])
                self.assertFalse(self.check()[0])

    def test_failed_scope_does_not_leave_reusable_evidence(self):
        with self.assertRaisesRegex(RuntimeError, 'stop'):
            with cache.validation_scope():
                self.assertTrue(self.check()[0])
                raise RuntimeError('stop')
        self.assertIsNone(cache._validation.get())
        with mock.patch.object(cache, '_digest', wraps=cache._digest) as digest:
            self.assertTrue(self.check()[0])
            self.assertEqual(digest.call_count, 1)

    def test_nested_scope_seals_only_at_outer_resolution_boundary(self):
        with mock.patch.object(cache, '_digest', wraps=cache._digest) as digest:
            with cache.validation_scope():
                self.assertTrue(self.check()[0])
                with cache.validation_scope():
                    self.assertTrue(self.check()[0])
                self.assertEqual(digest.call_count, 1)
            self.assertEqual(digest.call_count, 2)

    def test_expired_closing_digest_prevents_success(self):
        digest = cache._digest
        calls = []
        def expire(path):
            calls.append(path)
            if len(calls) == 2:
                cache.budget._deadline.set(time.monotonic() - 1)
            return digest(path)
        with mock.patch.object(cache, '_digest', side_effect=expire):
            with self.assertRaises(subprocess.TimeoutExpired), cache.budget.limit(2.5):
                with cache.validation_scope():
                    self.assertTrue(self.check()[0])
        self.assertIsNone(cache._validation.get())

    def test_real_multiblock_digest_includes_final_partial_block(self):
        raw = bytes(range(256)) * 2048 + b'last-partial-block'
        self.binary.write_bytes(raw)
        self.assertEqual(cache._digest(self.binary), hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
