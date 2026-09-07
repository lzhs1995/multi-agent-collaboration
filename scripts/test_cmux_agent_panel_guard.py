#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("cmux_agent_panel_guard.py")
SPEC = importlib.util.spec_from_file_location("cmux_agent_panel_guard", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class PanelGuardTests(unittest.TestCase):
    def assert_allowed(self, command: str) -> None:
        ok, message = GUARD.validate_command(command)
        self.assertTrue(ok, message)

    def assert_blocked(self, command: str) -> None:
        ok, message = GUARD.validate_command(command)
        self.assertFalse(ok, message)

    def test_existing_surface_coordination_is_allowed(self) -> None:
        self.assert_allowed(
            'rtk cmux-agent ask surface:36 "STATUS: continue the existing task with Claude"'
        )
        self.assert_allowed("rtk cmux-agent read surface:36 120")

    def test_raw_task_dispatch_without_finalized_pack_entry_is_blocked(self) -> None:
        self.assert_blocked(
            'rtk cmux-agent ask surface:36 "TASK:\\nreview this implementation"'
        )
        self.assert_blocked(
            'rtk cmux send --surface surface:36 -- "TASK:\\nrun the task"'
        )

    def test_callback_and_guarded_task_entry_remain_allowed(self) -> None:
        self.assert_allowed(
            'rtk cmux-agent ask surface:164 "DONE|task|nonce|REPORT=/tmp/report.md"'
        )
        self.assert_allowed(
            "rtk python3 dispatch.py cmux_bridge.submit_task_pack"
        )

    def test_direct_agent_launch_is_blocked(self) -> None:
        self.assert_blocked('rtk claude --resume "existing-id"')
        self.assert_blocked('rtk claude-safe-resume "existing-id"')
        self.assert_blocked("rtk claude-pinned")
        self.assert_blocked("env FOO=1 nohup codex")
        self.assertTrue(GUARD._shell_starts_agent_launch("setup && claude"))

    def test_exact_history_pinned_same_session_recovery_is_allowed_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "132509c7-e356-455d-af8d-645a9d832e5a"
            history = root / f"{session_id}.jsonl"
            history.write_bytes(b'{"sessionId":"same"}\n')
            auth = root / "recovery.json"
            auth.write_text(json.dumps({
                "schemaVersion": 1,
                "action": "resume-existing-executor-session",
                "executor": "claude",
                "sessionId": session_id,
                "previousSurfaceMissing": True,
                "requireSameSession": True,
                "forbidReplacement": True,
                "oneTimeByHistoryPin": True,
                "historyPath": str(history),
                "historyBytes": history.stat().st_size,
                "historySha256": hashlib.sha256(history.read_bytes()).hexdigest(),
                "recoverySurfaceRef": "surface:172",
            }), encoding="utf-8")
            command = (
                f"rtk env {GUARD.RECOVERY_AUTH_ENV}={auth} "
                f"claude --resume {session_id}"
            )
            self.assert_allowed(command)
            self.assert_allowed(
                f"rtk cmux send --surface surface:172 {command!r}"
            )
            self.assert_blocked(
                f"rtk cmux send --surface surface:173 {command!r}"
            )

            replacement = command.replace(session_id, "replacement-id", 1)
            self.assert_blocked(replacement)

            history.write_bytes(history.read_bytes() + b'{"appended":true}\n')
            self.assert_blocked(command)

    def test_agent_launch_pasted_into_terminal_is_blocked(self) -> None:
        self.assert_blocked(
            'rtk cmux send --surface surface:170 "claude --resume replacement"'
        )
        self.assert_blocked('rtk cmux-agent send surface:170 "claude"')

    def test_mentions_of_claude_are_not_launches(self) -> None:
        self.assert_allowed(
            'rtk cmux-agent ask surface:36 "Continue with Claude on the existing surface"'
        )
        self.assert_allowed("rtk rg -n Claude README.md")

    def test_non_shell_tool_payload_is_not_treated_as_a_command(self) -> None:
        forbidden_example = "cmux new-" + "surface followed by claude"
        payload = {"toolName": "apply_patch", "input": {"patch": forbidden_example}}
        self.assertEqual(GUARD._extract_command(payload), "")

    def test_agent_new_surface_is_always_blocked(self) -> None:
        self.assert_blocked("rtk cmux new-surface --type agent-session --provider claude")
        self.assert_blocked("rtk cmux new-surface && claude")

    def test_non_agent_surface_and_side_split_remain_allowed(self) -> None:
        self.assert_allowed("rtk cmux new-surface --type browser")
        self.assert_allowed(
            "rtk cmux new-split right --surface surface:164 --focus true"
        )

    def test_harness_double_authorization_path_is_allowed(self) -> None:
        self.assert_allowed(
            "rtk python3 mac_harness.py preflight --task-id t "
            "--executor claude --spawn --spawn-authorized"
        )


if __name__ == "__main__":
    unittest.main()
