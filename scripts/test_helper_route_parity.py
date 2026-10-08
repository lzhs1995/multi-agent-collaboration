"""固定 helper 的完整字节和同版 adapter 路由核验，不执行任何终端输入。"""
import json
import shlex
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mac_harness as harness
import render_cmux_agent as renderer
import cmux_workspace_guard as workspace


class HelperRouteParityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.helper = self.root / "cmux-agent"
        self.release = Path(harness.__file__).resolve().parents[1]
        self.raw = renderer.render((self.release / "tests/fixtures/cmux-agent-legacy.sh").read_bytes(),
            expected_sha256=renderer.BASELINE_SHA256, python=sys.executable,
            adapter=self.release / "scripts/cmux_agent_adapter.py")
        self.helper.write_bytes(self.raw)
        self.helper.chmod(0o755)
        finder = patch.object(harness, "find_external_helper", return_value=self.helper)
        finder.start()
        self.addCleanup(finder.stop)

    def test_rendered_helper_passes_without_obsolete_detector(self):
        result = harness.check_helper_parity()
        self.assertEqual(result["status"], "PARITY_OK")
        self.assertIs(result["route_verified"], True)
        self.assertEqual(result["adapter"], str(self.release / "scripts/cmux_agent_adapter.py"))
        args = SimpleNamespace(artifact_root=str(self.root), task_id="route-test", callback_transport="auto")
        harness.cmd_helper_parity(args)
        self.assertTrue(json.loads((self.root / "helper-parity.json").read_text())["route_verified"])

    def test_matching_exec_plus_extra_sender_never_passes_or_executes(self):
        sentinel = self.root / "MUST_NOT_EXIST"
        self.helper.write_bytes(self.raw + ('\ntouch "' + str(sentinel) + '"\n').encode())
        result = harness.check_helper_parity()
        self.assertEqual(result["status"], "HELPER_ROUTE_INVALID")
        self.assertFalse(result["route_verified"])
        self.assertFalse(sentinel.exists())

    def test_foreign_release_or_missing_isolation_rejected(self):
        for raw in (self.raw.replace(b" -I -B ", b" -B ", 1),
                    self.raw.replace(str(self.release).encode(), str(self.root).encode(), 1)):
            self.helper.write_bytes(raw)
            self.assertEqual(harness.check_helper_parity()["status"], "HELPER_ROUTE_INVALID")

    def test_legacy_screen_parity_does_not_authorize_helper_delivery(self):
        from test_r3_hardening import HelperParityTests
        self.helper.write_text("#!/usr/bin/env bash\n" + HelperParityTests.FIXED_FN + "\n")
        self.assertEqual(harness.check_helper_parity()["status"], "PARITY_OK")
        self.assertFalse(harness.check_helper_parity()["route_verified"])
        args = SimpleNamespace(artifact_root=str(self.root), task_id="legacy-test", callback_transport="auto")
        with self.assertRaises(SystemExit):
            harness.cmd_helper_parity(args)
        args.callback_transport = "bridge"
        harness.cmd_helper_parity(args)

    def test_workspace_guard_admits_only_verified_direct_helper(self):
        with patch.object(workspace.shutil, 'which', return_value=str(self.helper)):
            for command in ('rtk cmux-agent ask surface:2 "STATUS: review ready"',
                            'rtk proxy cmux-agent send surface:2 "STATUS: review ready"',
                            shlex.quote(str(self.helper)) + ' broadcast "STATUS: review ready"'):
                with self.subTest(command=command):
                    self.assertTrue(workspace.validate_command(command)[0])

    def test_workspace_guard_rejects_legacy_changed_and_foreign_route(self):
        with patch.object(workspace.shutil, 'which', return_value=str(self.helper)):
            for raw in (b'#!/bin/sh\ncmux send "$@"\n', self.raw + b'\necho changed\n',
                        self.raw.replace(str(self.release).encode(), str(self.root).encode(), 1),
                        self.raw.replace(b' -I -B ', b' -B ', 1)):
                with self.subTest(raw=raw[:60]):
                    self.helper.write_bytes(raw)
                    self.assertFalse(workspace.validate_command('rtk cmux-agent ask surface:2 hi')[0])

    def test_workspace_guard_rejects_wrappers_overrides_and_extra_commands(self):
        with patch.object(workspace.shutil, 'which', return_value=str(self.helper)):
            for command in ('env PATH=/tmp rtk cmux-agent ask surface:2 hi',
                            'PATH=/tmp rtk cmux-agent ask surface:2 hi',
                            'rtk proxy sh -c "cmux-agent ask surface:2 hi"',
                            'rtk cmux-agent ask surface:2 hi; cmux send surface:2 hi',
                            'rtk cmux-agent ask surface:2 "$(touch /tmp/unwanted)"',
                            'rtk cmux-agent ask surface:2 hi > /tmp/log',
                            'rtk cmux send --surface surface:2 hi',
                            'rtk cmux send-key --surface surface:2 enter'):
                with self.subTest(command=command):
                    self.assertFalse(workspace.validate_command(command)[0])

    def test_workspace_guard_rejects_non_executable_or_other_python(self):
        with patch.object(workspace.shutil, 'which', return_value=str(self.helper)):
            self.helper.chmod(0o644)
            self.assertFalse(workspace.validate_command('rtk cmux-agent ask surface:2 hi')[0])
            self.helper.chmod(0o755)
            other = self.root / 'python'
            other.write_text('#!/bin/sh\nexit 0\n')
            other.chmod(0o755)
            self.helper.write_bytes(renderer.render(
                (self.release / 'tests/fixtures/cmux-agent-legacy.sh').read_bytes(),
                expected_sha256=renderer.BASELINE_SHA256, python=other,
                adapter=self.release / 'scripts/cmux_agent_adapter.py'))
            self.assertFalse(workspace.validate_command('rtk cmux-agent ask surface:2 hi')[0])


if __name__ == "__main__":
    unittest.main()
