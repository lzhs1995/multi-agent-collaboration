"""Native FD completeness, race rejection and real Darwin FD parity."""
import ctypes as C
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import cmux_darwin_fds as native
import cmux_identity_budget as budget
import cmux_official_foreground as foreground


class FakeLib:
    def __init__(self):
        self.entries = [(3, 1), (4, 2), (5, 1)]
        self.calls = 0
        self.list_result = None
        self.info_result = None
        self.changed = False
        self.flags = {3: 3, 5: 1}
        self.path = b'/private/tmp/rollout-test.jsonl'

    def proc_pidinfo(self, pid, flavor, arg, out, size):
        self.calls += 1
        if self.list_result is not None:
            return self.list_result
        entries = self.entries + ([(6, 1)] if self.changed and self.calls > 1 else [])
        for pos, (fd, kind) in enumerate(entries):
            out[pos].fd, out[pos].fdtype = fd, kind
        return len(entries) * C.sizeof(native.FDInfo)

    def proc_pidfdinfo(self, pid, fd, flavor, pointer, size):
        if self.info_result is not None:
            return self.info_result
        out = C.cast(pointer, C.POINTER(native.VNodeFD)).contents
        out.file.flags = self.flags[fd]
        out.vnode.info.stat.ino = 987654321
        if len(self.path) == native.MAX_PATH:
            C.memmove(C.addressof(out) + 176, self.path, native.MAX_PATH)
        else:
            out.vnode.path = self.path
        return C.sizeof(out)


class DarwinFDTests(unittest.TestCase):
    def setUp(self):
        self.lib = FakeLib()
        patch = mock.patch.object(native, '_library', return_value=self.lib)
        patch.start()
        self.addCleanup(patch.stop)

    def test_only_actual_writable_vnodes_selected(self):
        self.assertEqual(native.frames(123), [{'f': '3', 'a': 'u', 'i': '987654321',
                                               'n': '/private/tmp/rollout-test.jsonl'}])
        self.assertEqual(self.lib.calls, 2)

    def test_write_only_access_preserved(self):
        self.lib.flags[3] = 2
        self.assertEqual(native.frames(123)[0]['a'], 'w')

    def test_failed_short_oversized_and_full_inventory_refused(self):
        for count in (-1, 0, 7, (native.MAX_FDS + 1) * 8, (native.MAX_FDS + 2) * 8):
            self.lib.list_result = count
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'descriptor list'):
                native.frames(123)

    def test_duplicate_or_invalid_fd_refused(self):
        for entries in ([(3, 1), (3, 1)], [(-1, 1)]):
            self.lib.entries = entries
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, 'invalid descriptor'):
                native.frames(123)

    def test_vanished_denied_or_short_vnode_refused(self):
        for result in (-1, 0, 1199, 1201):
            self.lib.info_result = result
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, 'vnode unavailable'):
                native.frames(123)

    def test_added_writer_during_inventory_refused(self):
        self.lib.changed = True
        with self.assertRaisesRegex(ValueError, 'list changed'):
            native.frames(123)

    def test_empty_or_truncated_writable_path_refused(self):
        for value in (b'', b'x' * 1024):
            self.lib.path = value
            with self.subTest(length=len(value)), self.assertRaisesRegex(ValueError, 'vnode path'):
                native.frames(123)

    def test_second_writer_remains_ambiguous_even_same_path(self):
        self.lib.flags[5] = 2
        with mock.patch.object(native, 'supported', return_value=True):
            with self.assertRaisesRegex(ValueError, 'one writable rollout'):
                foreground.descriptors({'pid': 123})

    def test_native_failure_never_falls_back_to_lsof(self):
        self.lib.info_result = -1
        with mock.patch.object(native, 'supported', return_value=True), \
                mock.patch.object(foreground, '_lsof_descriptors') as fallback:
            with self.assertRaises(ValueError):
                foreground.descriptors({'pid': 123})
            fallback.assert_not_called()

    def test_budget_is_preserved(self):
        with self.assertRaises(subprocess.TimeoutExpired), budget.limit(-1):
            native.frames(123)

    def test_pid_not_coerced_or_truncated(self):
        for pid in (True, '123', 0, -1, 2**32):
            with self.subTest(pid=pid), self.assertRaisesRegex(ValueError, 'valid pid'):
                native.frames(pid)


@unittest.skipUnless(sys.platform == 'darwin', 'public Darwin libproc integration')
class LiveDarwinFDTests(unittest.TestCase):
    def test_kernel_descriptor_matches_lsof_and_second_writer_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'rollout-live.jsonl'
            code = ('import sys; a=open(sys.argv[1],"ab"); print("ready",flush=True); '
                    'sys.stdin.readline(); b=open(sys.argv[1],"ab"); '
                    'print("second",flush=True); sys.stdin.readline()')
            child = subprocess.Popen([sys.executable, '-B', '-c', code, str(path)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                client = {'pid': child.pid}
                self.assertEqual(foreground.descriptors(client), foreground._lsof_descriptors(client))
                child.stdin.write('next\n')
                child.stdin.flush()
                self.assertEqual(child.stdout.readline().strip(), 'second')
                with self.assertRaisesRegex(ValueError, 'one writable rollout'):
                    foreground.descriptors(client)
            finally:
                child.communicate('\n', timeout=5)
                if child.poll() is None:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    unittest.main()
