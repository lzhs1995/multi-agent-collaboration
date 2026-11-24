from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cmux_herdr_status_sync import plan_workspace_pills_and_description, short_pane_token


class CmuxHerdrStatusSyncTests(unittest.TestCase):
    def test_six_or_fewer_panes_stay_expanded_including_unknown_shell(self):
        tabs = [{"tab_id": "w1:t1", "tab_index": 1, "label": "T1-CMA主控"}]
        panes = [
            {
                "pane_id": "w1:p1",
                "tab_id": "w1:t1",
                "tab_index": 1,
                "effective_kind": "codex",
                "agent_name": "t1-sup-cx",
                "pane_label": "[T1·L|p1] SUP-CX · 主管",
                "agent_status": "idle",
            },
            {
                "pane_id": "w1:p2",
                "tab_id": "w1:t1",
                "tab_index": 1,
                "effective_kind": "shell",
                "agent_name": None,
                "pane_label": "[T1·R|p2] SH-待命 · 终端",
                "agent_status": "unknown",
            },
            {
                "pane_id": "w1:p3",
                "tab_id": "w1:t1",
                "tab_index": 1,
                "effective_kind": "claude",
                "agent_name": "t1-rev-cc1",
                "pane_label": "[T1·CU|p3] REV-CC1 · 审阅",
                "agent_status": "working",
            },
        ]
        plan = plan_workspace_pills_and_description(
            tabs, panes, existing_herdr_keys={"herdr:w1:p1", "herdr:tab:w1:t1"}
        )
        self.assertFalse(plan["folded"])
        self.assertEqual(set(plan["desired_pills"].keys()), {"herdr:w1:p1", "herdr:w1:p2", "herdr:w1:p3"})
        self.assertEqual(plan["stale_keys"], ["herdr:tab:w1:t1"])
        self.assertIn("🔨1(t1-rev-cc1)", plan["description"])
        self.assertIn("[1T/3P]", plan["description"])

    def test_nine_panes_across_two_tabs_fold_idle_by_tab_and_pin_working_on_top(self):
        tabs = [
            {"tab_id": "w1:t1", "tab_index": 1, "label": "T1-主控双审"},
            {"tab_id": "w1:t6", "tab_index": 2, "label": "T2-升级规划"},
        ]
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "codex", "agent_name": "t1-sup-cx1", "pane_label": "[T1·L|p1] SUP-CX1 · 主线", "agent_status": "idle"},
            {"pane_id": "w1:p2", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "claude", "agent_name": "t1-rev-cc1", "pane_label": "[T1·CU|p2] REV-CC1 · 文风", "agent_status": "working"},
            {"pane_id": "w1:p3", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "shell", "agent_name": None, "pane_label": "[T1·RU|p3] SH · 备用", "agent_status": "unknown"},
            {"pane_id": "w1:p4", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "claude", "agent_name": "t1-rev-cc2", "pane_label": "[T1·CD|p4] REV-CC2 · 核验", "agent_status": "idle"},
            {"pane_id": "w1:pA", "tab_id": "w1:t1", "tab_index": 1, "effective_kind": "codex", "agent_name": "t1-exe-cx2", "pane_label": "[T1·RD|pA] EXE-CX2 · 实施", "agent_status": "idle"},
            {"pane_id": "w1:pB", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "codex", "agent_name": "t2-sup-s40", "pane_label": "[T2·LU|pB] SUP-S40 · 维护", "agent_status": "blocked"},
            {"pane_id": "w1:pC", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "claude", "agent_name": "t2-rev-cc", "pane_label": "[T2·RU|pC] REV-CC · 顾问", "agent_status": "idle"},
            {"pane_id": "w1:pD", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "codex", "agent_name": "t2-sup-upg", "pane_label": "[T2·LD|pD] SUP-UPG · 实验", "agent_status": "idle"},
            {"pane_id": "w1:pE", "tab_id": "w1:t6", "tab_index": 2, "effective_kind": "agy", "agent_name": "t2-arc-agy", "pane_label": "[T2·RD|pE] ARC-AGY · 规划", "agent_status": "working"},
        ]
        existing = {f"herdr:{p['pane_id']}" for p in panes}
        plan = plan_workspace_pills_and_description(tabs, panes, existing_herdr_keys=existing)
        self.assertTrue(plan["folded"])
        # 3 active/blocked panes + 2 tab idle summaries = 5 clean pills instead of 9!
        self.assertEqual(
            set(plan["desired_pills"].keys()),
            {"herdr:w1:p2", "herdr:w1:pB", "herdr:w1:pE", "herdr:tab:w1:t1", "herdr:tab:w1:t6"},
        )
        self.assertEqual(plan["desired_pills"]["herdr:w1:pB"]["priority"], 90)
        self.assertEqual(plan["desired_pills"]["herdr:w1:p2"]["priority"], 80)
        self.assertEqual(plan["desired_pills"]["herdr:tab:w1:t1"]["priority"], 35)
        self.assertIn("T1-主控双审 (⏸4):", plan["desired_pills"]["herdr:tab:w1:t1"]["value"])
        self.assertIn("T2-升级规划 (⏸2):", plan["desired_pills"]["herdr:tab:w1:t6"]["value"])
        self.assertIn("herdr:w1:p1", plan["stale_keys"])
        self.assertIn("⚠️1(t2-sup-s40)", plan["description"])
        self.assertIn("[2T/9P]", plan["description"])

    def test_short_pane_token_extracts_explicit_role_when_agent_name_absent(self):
        token = short_pane_token(
            {"pane_id": "w1:p3", "agent_name": None, "pane_label": "[T1·RU|p3] SH-备用 · 终端"}
        )
        self.assertEqual(token, "p3:SH-备用")


if __name__ == "__main__":
    unittest.main(verbosity=2)
