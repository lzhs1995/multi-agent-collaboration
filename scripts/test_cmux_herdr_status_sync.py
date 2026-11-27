from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cmux_herdr_status_sync as sync
from cmux_herdr_status_sync import (
    infer_kind_from_process_info,
    plan_workspace_pills_and_description,
)


class AttachDiscoveryTests(unittest.TestCase):
    live = "101 ttys001 Fri Nov 27 18:00:00 2026 /opt/homebrew/bin/herdr --session default\n"
    primary_timeout = subprocess.TimeoutExpired(["ps", "-axo", "pid=,tty=,command="], 8)

    @staticmethod
    def result(stdout="", returncode=0):
        return subprocess.CompletedProcess(["ps"], returncode, stdout, "query failed" if returncode else "")

    def setUp(self):
        self.workspace = sync.PREFERRED[0][1]
        self.orphan_workspace = sync.PREFERRED[1][1]
        self.tree = {"workspaces": [
            {"id": self.workspace, "panes": [{"surfaces": [
                {"id": "live-surface", "type": "terminal", "tty": "ttys001", "title": "Herdr · default"},
            ]}]},
            {"id": self.orphan_workspace, "panes": [{"surfaces": [
                {"id": "orphan-surface", "type": "terminal", "tty": "ttys002", "title": "Herdr · c4-multiagent"},
            ]}]},
        ]}
        self.reads = patch.object(sync, "run_cmd", side_effect=[json.dumps(self.tree), '{"workspaces": []}']).start()
        self.publish = patch.object(sync, "publish_target", return_value={"surface": "live-surface"}).start()
        self.addCleanup(patch.stopall)

    def assert_read_only_discovery(self):
        self.assertEqual([c.args[0] for c in self.reads.call_args_list], [
            ["cmux", "tree", "--id-format", "both", "--json"],
            ["cmux", "workspace", "list", "--json"],
        ])

    def test_normal_query_keeps_existing_publish_path_and_does_not_retry(self):
        self.tree["workspaces"] = self.tree["workspaces"][:1]
        self.reads.side_effect = [json.dumps(self.tree), '{"workspaces": []}']
        output = ("101 ttys001 /opt/homebrew/bin/herdr --session default\n"
                  "102 ttys001 herdr server\n103 ttys002 python3 cmux-herdr.py\n")
        with patch.object(sync.subprocess, "run", return_value=self.result(output)) as ps:
            report = sync.publish_once()
        self.assertEqual(report, {"results": [{"surface": "live-surface"}], "errors": []})
        ps.assert_called_once()
        self.assertEqual(ps.call_args.args[0], ["ps", "-axo", "pid=,tty=,command="])
        self.assertEqual(ps.call_args.kwargs["timeout"], 8)
        self.assertEqual(self.publish.call_args.args[4], {"ttys001": "default"})
        self.assertFalse(self.publish.call_args.kwargs["require_live"])

    def test_primary_timeout_recovers_with_tty_then_pid_queries_without_title_expansion(self):
        unrelated = "202 ttys002 Fri Nov 27 18:00:00 2026 -zsh\n"
        with patch.object(sync.subprocess, "run", side_effect=[
            self.primary_timeout, self.result(self.live + unrelated), self.result(self.live),
        ]) as ps:
            report = sync.publish_once()
        self.assertEqual(report["errors"], [])
        self.assertIn("recovered with TTY/PID recheck", report["warnings"][0])
        self.assertEqual(ps.call_count, 3)
        self.assertEqual(ps.call_args_list[1].args[0], [
            "ps", "-ww", "-t", "ttys001,ttys002", "-o", "pid=,tty=,lstart=,command=",
        ])
        self.assertEqual(ps.call_args_list[2].args[0], [
            "ps", "-ww", "-p", "101", "-o", "pid=,tty=,lstart=,command=",
        ])
        self.assertEqual([c.kwargs["timeout"] for c in ps.call_args_list], [8, 3, 3])
        self.publish.assert_called_once()
        self.assertEqual(self.publish.call_args.args[:2], ("default", self.workspace))
        self.assertTrue(self.publish.call_args.kwargs["require_live"])
        self.assert_read_only_discovery()

    def test_failed_rechecks_preserve_previous_sidebar_without_publishing(self):
        failures = [
            subprocess.TimeoutExpired(["ps", "-p", "101"], 3),
            self.result(returncode=1),
            self.result(""),
            self.result(self.live.replace("18:00:00", "18:01:00")),
            self.result(self.live.replace("ttys001", "ttys002")),
            self.result(self.live.replace("default", "different-session")),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                self.reads.reset_mock(side_effect=True)
                self.reads.side_effect = [json.dumps(self.tree), '{"workspaces": []}']
                with patch.object(sync.subprocess, "run", side_effect=[
                    self.primary_timeout, self.result(self.live), failure,
                ]):
                    with self.assertRaisesRegex(RuntimeError, "existing sidebar preserved.*scoped recheck"):
                        sync.publish_once()
                self.publish.assert_not_called()
                self.assert_read_only_discovery()

    def test_unreliable_scoped_queries_fail_before_pid_recheck_or_publish(self):
        conflict = self.live + self.live.replace("101 ", "102 ").replace("default", "other")
        failures = [
            subprocess.TimeoutExpired(["ps", "-t", "ttys001,ttys002"], 3),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            self.result(""), self.result("invalid row\n"), self.result(conflict),
            self.result(self.live.replace("ttys001", "ttys999")),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                self.reads.reset_mock(side_effect=True)
                self.reads.side_effect = [json.dumps(self.tree), '{"workspaces": []}']
                with patch.object(sync.subprocess, "run", side_effect=[self.primary_timeout, failure]) as ps:
                    with self.assertRaisesRegex(RuntimeError, "existing sidebar preserved"):
                        sync.publish_once()
                self.assertEqual(ps.call_count, 2)
                self.publish.assert_not_called()
                self.assert_read_only_discovery()

    def test_scoped_query_never_uses_empty_or_invalid_tty_selectors(self):
        for ttys in ([], ["??"], ["-t"], ["-t,ttys001"], ["ttys001\n"], [None]):
            with self.subTest(ttys=ttys):
                runner = Mock()
                with self.assertRaisesRegex(RuntimeError, "explicit valid terminal TTYs"):
                    sync.herdr_attach_for_ttys(ttys, runner=runner)
                runner.assert_not_called()

    def test_live_mapping_rejects_two_tty_hits_even_with_one_matching_title(self):
        surfaces = [
            {"id": "first", "tty": "ttys001", "title": "Herdr · default"},
            {"id": "second", "tty": "ttys002", "title": "Other title"},
        ]
        attaches = {"ttys001": "default", "ttys002": "default"}
        with self.assertRaisesRegex(RuntimeError, "requires one live tty surface; matched 2"):
            sync.find_host_surface("default", surfaces, attaches, require_live=True)
        self.assertEqual(sync.find_host_surface("default", surfaces, attaches), "first")


class CmuxHerdrStatusSyncTests(unittest.TestCase):
    def test_small_tab_renders_inline_tool_chips_with_zero_reply_text(self):
        tabs = [{"tab_id": "w1:t1", "tab_index": 1, "label": "T1-CMA主控双审"}]
        panes = [
            {
                "pane_id": "w1:p1",
                "tab_id": "w1:t1",
                "tab_index": 1,
                "effective_kind": "codex",
                "pane_label": "[T1·L|p1] SUP-CX · 主管",
                "terminal_title": "发布条件仍未齐：不应该出现在侧栏的具体回复正文",
                "agent_status": "idle",
            },
            {
                "pane_id": "w1:p2",
                "tab_id": "w1:t1",
                "tab_index": 1,
                "effective_kind": "shell",
                "pane_label": "[T1·R|p2] SH-待命 · 终端",
                "agent_status": "unknown",
            },
            {
                "pane_id": "w1:p3",
                "tab_id": "w1:t1",
                "tab_index": 1,
                "effective_kind": "claude",
                "pane_label": "[T1·CU|p3] REV-CC1 · 审阅",
                "terminal_title": "Long assistant reply that must never leak",
                "agent_status": "working",
            },
        ]
        plan = plan_workspace_pills_and_description(
            tabs,
            panes,
            existing_status_keys={"herdr:w1:p1", "herdr:tab:w1:t1", "codex", "git_branch"},
        )
        self.assertTrue(plan["tab_grouped"])
        self.assertEqual(set(plan["desired_pills"].keys()), {"herdr:tab:w1:t1"})
        pill = plan["desired_pills"]["herdr:tab:w1:t1"]
        self.assertEqual(pill["icon"], "hammer")
        self.assertEqual(pill["value"], "T1 CMA主控双: ⏸CX(L) ⏸SH 🔨CC(CU)")
        self.assertNotIn("发布条件", pill["value"])
        self.assertNotIn("Long assistant", pill["value"])
        # Clears legacy per-pane herdr pill and redundant `codex=Running`, leaves `git_branch` untouched
        self.assertEqual(plan["stale_keys"], ["codex", "herdr:w1:p1"])
        self.assertEqual(plan["description"], "Herdr · 1T/3P · 🔨1 ⏸2")

    def test_multi_tab_nine_panes_keep_stable_tab_order_and_compact_length(self):
        tabs = [
            {"tab_id": "w1:t1", "tab_index": 1, "label": "T1-复盘与双审"},
            {"tab_id": "w1:t6", "tab_index": 2, "label": "T2-双仓与规划"},
        ]
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "codex", "pane_label": "[T1·L|p1] SUP-CX1", "agent_status": "idle"},
            {"pane_id": "w1:p2", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "claude", "pane_label": "[T1·CU|p2] REV-CC1", "agent_status": "working"},
            {"pane_id": "w1:p3", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "shell", "pane_label": "[T1·RU|p3] SH", "agent_status": "unknown"},
            {"pane_id": "w1:p4", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "claude", "pane_label": "[T1·CD|p4] REV-CC2", "agent_status": "idle"},
            {"pane_id": "w1:pA", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "codex", "pane_label": "[T1·RD|pA] EXE-CX2", "agent_status": "idle"},
            {"pane_id": "w1:pB", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "codex", "pane_label": "[T2·LU|pB] SUP-S40", "agent_status": "blocked"},
            {"pane_id": "w1:pC", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "claude", "pane_label": "[T2·RU|pC] REV-CC", "agent_status": "idle"},
            {"pane_id": "w1:pD", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "codex", "pane_label": "[T2·LD|pD] SUP-UPG", "agent_status": "idle"},
            {"pane_id": "w1:pE", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "agy", "pane_label": "[T2·RD|pE] ARC-AGY", "agent_status": "working"},
        ]
        plan = plan_workspace_pills_and_description(tabs, panes, existing_status_keys=set())
        self.assertEqual(set(plan["desired_pills"].keys()), {"herdr:tab:w1:t1", "herdr:tab:w1:t6"})
        t1 = plan["desired_pills"]["herdr:tab:w1:t1"]
        t2 = plan["desired_pills"]["herdr:tab:w1:t6"]
        self.assertGreater(t1["priority"], t2["priority"])
        self.assertEqual(t1["value"], "T1 复盘与双审: 🔨CC(CU) · ⏸4(CC,CX×2,SH)")
        self.assertEqual(t2["icon"], "exclamationmark.triangle")
        self.assertEqual(t2["value"], "T2 双仓与规划: ⚠️CX(LU) ⏸CC(RU) ⏸CX(LD) 🔨AGY(RD)")
        self.assertEqual(plan["description"], "Herdr · 2T/9P · ⚠️1 🔨2 ⏸6")

    def test_magpie_and_agy_wrappers_resolve_to_exact_tool_kinds(self):
        self.assertEqual(
            infer_kind_from_process_info(
                {"foreground_processes": [{"name": "python3.14", "argv": ["python3", "/Users/lzhs/.local/bin/mg", "cc", "linxi", "-k", "linxi-s1"]}]}
            ),
            "claude",
        )
        self.assertEqual(
            infer_kind_from_process_info(
                {"foreground_processes": [{"name": "agy.real", "argv0": "/Users/lzhs/.local/bin/agy.real", "argv": ["agy.real"]}]}
            ),
            "agy",
        )

    def test_unknown_state_stays_visible_inline_and_folded(self):
        tabs = [{"tab_id": "t1", "tab_index": 1}]
        unknown = {"pane_id": "p1", "tab_id": "t1", "effective_kind": "codex", "agent_status": "unknown"}
        plan = plan_workspace_pills_and_description(tabs, [unknown], set())
        self.assertEqual(plan["desired_pills"]["herdr:tab:t1"]["value"], "T1: ❔CX(p1)")
        self.assertEqual(plan["desired_pills"]["herdr:tab:t1"]["icon"], "questionmark.circle")
        self.assertIn("❔1", plan["description"])
        self.assertNotIn("⏸", plan["description"])
        rows = [unknown] + [{"pane_id": f"p{i}", "tab_id": "t1", "effective_kind": "claude", "agent_status": "working"} for i in range(2,6)]
        plan = plan_workspace_pills_and_description(tabs, rows, set())
        self.assertIn("❔1(CX)",plan["desired_pills"]["herdr:tab:t1"]["value"])
        self.assertIn("❔1",plan["description"])

    def test_non_client_arguments_and_foreground_jobs_are_unknown(self):
        for argv in (["cat","codex-report.txt"], ["python3","analysis.py"], ["sh","-c","codex"], ["python3","-c","import claude"], ["cat","mg","cx","station"]):
            with self.subTest(argv=argv):
                self.assertEqual(infer_kind_from_process_info({"foreground_processes":[{"argv0":argv[0],"argv":argv}]}),"unknown")
        self.assertEqual(infer_kind_from_process_info({}),"unknown")
        self.assertEqual(infer_kind_from_process_info({"foreground_processes":[{"argv0":"codex","argv":["codex"]},{"argv0":"python3","argv":["python3","job.py"]}]}),"unknown")

    def test_supported_executable_wrappers_and_idle_shell(self):
        for argv, expected in [(["codex"],"codex"),(["/path/codex-aarch64-apple-darwin"],"codex"),(["mg","cx","station"],"codex"),(["python3.14","/path/mg","cc","station"],"claude"),(["sh","/path/agy"],"agy"),(["node","/path/claude"],"claude"),(["-zsh"],"shell")]:
            with self.subTest(argv=argv):
                self.assertEqual(infer_kind_from_process_info({"foreground_processes":[{"argv0":argv[0],"argv":argv}]}),expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
