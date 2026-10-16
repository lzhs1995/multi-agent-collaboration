"""Synthetic copies of the measured 2026-10-13 frame; no live input."""
import unittest
from unittest.mock import patch

import cmux_bridge as b
from native_test_support import NativeFixture
from test_claude_footer_parser_20261008 import with_draft


MONITOR = "⏵⏵ bypass permissions on · 1 monitor · ← for agents"
SHELL = "⏵⏵ bypass permissions on · 1 shell · ← for agents"
NORMAL = "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
SPINNER = "✽ Skedaddling… (8m 40s · ↓ 9.8k tokens)"
GOAL = " " * 45 + "◎ /goal active (10h)"
SUMMARY = "✻ Cogitated for 14m 15s · 1 monitor still running"


def screen(*, status=SPINNER, goal=GOAL, footer=MONITOR, tool=False):
    border = "─" * 100
    return "\n".join((
        SUMMARY, "", status, goal, border, "❯\u00a0", border,
        "  [Opus 5] │ tasks/example git:(main) │ ⏱️  1309h 45m",
        "  上下文 ██░░░░░░░░ 18%", "  2 CLAUDE.md | 9 MCPs | 7 钩子",
        "  ◐ Bash: .../py... | ✓ Bash ×7 | ✓ Read ×5 | +1 more" if tool else
        "  ✓ Bash ×8 | ✓ Read ×5 | ✓ Write ×4 | ✓ ScheduleWakeup ×3",
        "  ✓ general-purpose [opus-5]: Audit release (<1s)", "  " + footer,
    )) + "\n"


class MonitorGoalTests(unittest.TestCase):
    def test_monitor_frame_is_empty_but_spinner_remains_active(self):
        value = screen()
        self.assertEqual(b.receiver_input_kind(value), "AGENT_TUI")
        self.assertTrue(b.compose_block_is_empty(value))
        self.assertTrue(b._queued_or_active_input(value))
        with self.assertRaises(b.DispatchUnconfirmed):
            b.require_clearable_agent_input(value, "offline-peer")

    def test_post_monitor_normal_footer_still_sees_goal_and_tool(self):
        for tool in (False, True):
            with self.subTest(tool=tool):
                value = screen(footer=NORMAL, tool=tool)
                self.assertTrue(b.compose_block_is_empty(value))
                self.assertTrue(b._queued_or_active_input(value))

    def test_goal_does_not_itself_mean_busy_or_hide_completed_summary(self):
        for status in (SUMMARY, "✻ Baked for 2m 0s", ""):
            with self.subTest(status=status):
                value = screen(status=status)
                self.assertFalse(b._queued_or_active_input(value))
                b.require_clearable_agent_input(value, "offline-peer")

    def test_active_tool_is_independent_of_spinner(self):
        self.assertTrue(b._queued_or_active_input(screen(status=SUMMARY, tool=True)))

    def test_measured_dot_spinner_is_active_only_before_the_frame(self):
        dot = SPINNER.replace('✽', '·', 1)
        self.assertTrue(b._queued_or_active_input(screen(status=dot)))
        draft = with_draft(screen(status=SUMMARY), dot)
        self.assertEqual(b.compose_block_text(draft), dot)
        self.assertFalse(b._queued_or_active_input(draft))

    def test_one_shell_footer_preserves_real_activity_and_drafts(self):
        for status, tool, busy in ((SUMMARY, False, False), (SPINNER, False, True),
                                    (SUMMARY, True, True)):
            with self.subTest(status=status, tool=tool):
                value = screen(status=status, tool=tool, footer=SHELL)
                self.assertTrue(b.compose_block_is_empty(value))
                self.assertEqual(b._queued_or_active_input(value), busy)
        value = with_draft(screen(status=SUMMARY, footer=SHELL), SHELL)
        self.assertEqual(b.compose_block_text(value), SHELL)
        self.assertFalse(b.compose_block_is_empty(value))
        queued = screen(status="Messages to be submitted after completion:\n  queued payload",
                        footer=SHELL)
        self.assertTrue(b._queued_or_active_input(queued))

    def test_unmeasured_shell_shapes_remain_unparsed(self):
        for footer in (SHELL + " extra", SHELL.replace("1 shell", "0 shell"),
                       SHELL.replace("1 shell", "1 shells"),
                       SHELL.replace("1 shell", "01 shell"),
                       SHELL.replace("1 shell", "2 shells"),
                       SHELL.replace(" · ← for agents", "")):
            with self.subTest(footer=footer):
                value = screen(status=SUMMARY, footer=footer)
                self.assertIsNone(b.compose_rendered_text(value))
                with self.assertRaisesRegex(b.DispatchUnconfirmed, "COMPOSE_UNPARSED"):
                    b.require_clearable_agent_input(value, "offline-peer")

    def test_unknown_footer_is_unparsed_not_history_busy_or_a_draft(self):
        for footer in (MONITOR + " extra", "⏵⏵ bypass permissions on · monitor",
                       MONITOR.replace("1 monitor", "0 monitor"),
                       MONITOR.replace("1 monitor", "one monitor"),
                       MONITOR.replace("1 monitor", "1 monitors"),
                       MONITOR.replace("1 monitor", "01 monitor"),
                       MONITOR.replace(" · ← for agents", "")):
            with self.subTest(footer=footer):
                value = screen(status=SUMMARY, footer=footer)
                self.assertEqual(b.receiver_input_kind(value), "UNKNOWN")
                self.assertIsNone(b.compose_rendered_text(value))
                self.assertFalse(b.compose_block_is_empty(value))
                self.assertFalse(b._queued_or_active_input(value))
                with NativeFixture(provider="claude") as native, patch.object(
                        b, "read_screen", return_value=value):
                    with self.assertRaisesRegex(b.DispatchUnconfirmed, "COMPOSE_UNPARSED"):
                        b.submit_text("offline-peer", "STATUS: MONITOR0435", marker="MONITOR0435")
                    native.send.assert_not_called()
                    native.key.assert_not_called()

    def test_goal_and_monitor_shaped_drafts_are_preserved(self):
        for draft in (MONITOR, GOAL, SPINNER, "first\n  " + MONITOR + "\n  last"):
            with self.subTest(draft=draft):
                value = with_draft(screen(status=SUMMARY), draft)
                self.assertEqual(b.compose_block_text(value), draft)
                self.assertFalse(b.compose_block_is_empty(value))
                self.assertFalse(b._queued_or_active_input(value))

    def test_queue_before_border_remains_active(self):
        value = screen(status="Messages to be submitted after completion:\n  queued payload")
        self.assertTrue(b._queued_or_active_input(value))

    def test_exact_goal_only_skips_known_state_and_duration(self):
        for goal in (GOAL, GOAL.replace("10h", "1m 2s"),
                     GOAL.replace("10h", "1d"), GOAL + "\n\n"):
            with self.subTest(goal=goal):
                self.assertTrue(b._queued_or_active_input(screen(goal=goal)))
        for goal in (GOAL + " extra", GOAL.replace("active", "invented"),
                     GOAL.replace("10h", "soon"), GOAL.replace("10h", "1d 2h"),
                     GOAL.replace("10h", "1w")):
            with self.subTest(goal=goal):
                self.assertIsNone(b._CLAUDE_GOAL_ROW_RE.fullmatch(goal))
                value = screen(goal=goal)
                self.assertIsNone(b.compose_rendered_text(value))
                with self.assertRaisesRegex(b.DispatchUnconfirmed, "COMPOSE_UNPARSED"):
                    b.require_clearable_agent_input(value, "offline-peer")

    def test_day_goal_in_each_measured_location_keeps_drafts_and_activity(self):
        day = "◎ /goal active (1d)"
        for placement in ("before", "model", "clear_hint"):
            for active in (False, True):
                value = screen(status=SPINNER if active else SUMMARY, goal="")
                if placement == "before":
                    value = screen(status=SPINNER if active else SUMMARY, goal=day)
                elif placement == "model":
                    value = value.replace("1309h 45m", "1309h 45m " + day)
                else:
                    value += "  new task? /clear to save 85k tokens · " + day + "\n"
                with self.subTest(placement=placement, active=active):
                    self.assertTrue(b.compose_block_is_empty(value))
                    self.assertEqual(b._queued_or_active_input(value), active)
                    draft = with_draft(value, day)
                    self.assertEqual(b.compose_block_text(draft), day)
                    self.assertFalse(b.compose_block_is_empty(draft))
        # Whole days are a Goal display, not a new grammar for tool/time rows.
        self.assertIsNone(b._CLAUDE_SUMMARY_RE.fullmatch("✓ Bash: completed (1d)"))

    def test_idle_monitor_frame_delivers_once_with_exact_native_receipt(self):
        for footer in (MONITOR, SHELL):
            before = screen(status=SUMMARY, footer=footer)
            text = "STATUS: MONITOR0435"
            with self.subTest(footer=footer), NativeFixture(provider="claude") as native, patch.object(
                    b, "read_screen", side_effect=native.ready_screens(before, with_draft(before, text))):
                native.key.side_effect = native.receipt_on_key(text)
                result = b.submit_text("offline-peer", text, marker="MONITOR0435")
                self.assertTrue(result["confirmed"])
                native.send.assert_called_once()
                native.key.assert_called_once_with("offline-peer", "enter")


if __name__ == "__main__":
    unittest.main()
