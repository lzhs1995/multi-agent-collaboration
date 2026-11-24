from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cmux_herdr_status_sync import (
    infer_kind_from_process_info,
    plan_workspace_pills_and_description,
)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
