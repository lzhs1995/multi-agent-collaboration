"""Synthetic B0023/B0024 footer shapes; never send input to live clients."""
import unittest
from unittest.mock import patch

import cmux_bridge as b
from native_test_support import NativeFixture
from test_claude_footer_parser_20261008 import with_draft


GOAL = "◎ /goal active (59m)"
MODEL = "[linxi/claude-opus-5-5[1M]]"


def frame(*, provider="linxi", goal_position="inline", status="", tool=False):
    border = "─" * 100
    model = f"  [{provider}/claude-opus-5-5[1M]] │ tasks/example git:(main) │ ⏱️ 847h 26m"
    if goal_position == "inline":
        model += " " + GOAL
    rows = [status, border, "❯\u00a0", border, model,
            "  上下文 ██░░░░░░░░ 18%", "  2 CLAUDE.md | 9 MCPs | 7 钩子",
            "  ◐ Bash: example" if tool else "  ✓ Bash ×8 | ✓ Read ×5",
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)"]
    if goal_position == "final":
        rows.append(" " * 45 + GOAL)
    return "\n".join(rows) + "\n"


class ProviderGoalFooterTests(unittest.TestCase):
    def assert_unparsed(self, value):
        self.assertIsNone(b._claude_bordered_compose(value))
        self.assertIsNone(b.compose_rendered_text(value))
        self.assertFalse(b.compose_block_is_empty(value))
        self.assertEqual(b.receiver_input_kind(value), "UNKNOWN")
        with NativeFixture(provider="claude") as native:
            with self.assertRaises(b.DispatchUnconfirmed):
                b.require_clearable_agent_input(value, "offline-peer")
            native.send.assert_not_called()
            native.key.assert_not_called()

    def test_measured_provider_and_goal_positions_are_empty(self):
        for provider in ("linxi", "baibei"):
            for position in ("inline", "final", "none"):
                with self.subTest(provider=provider, position=position):
                    value = frame(provider=provider, goal_position=position)
                    self.assertEqual(b.receiver_input_kind(value), "AGENT_TUI")
                    self.assertEqual(b.compose_rendered_text(value), "")
                    self.assertTrue(b.compose_block_is_empty(value))
                    self.assertFalse(b._queued_or_active_input(value))
                    b.require_clearable_agent_input(value, "offline-peer")

    def test_footer_shaped_drafts_are_preserved_verbatim(self):
        for position in ("inline", "final"):
            for draft in (MODEL, GOAL, MODEL + "\n  " + GOAL,
                          "STATUS: exact payload\n  trailing  "):
                with self.subTest(position=position, draft=draft):
                    value = with_draft(frame(goal_position=position), draft)
                    self.assertEqual(b.compose_block_text(value), draft)
                    self.assertFalse(b.compose_block_is_empty(value))
                    with NativeFixture(provider="claude"), \
                            patch.object(b, "read_screen", return_value=value), \
                            patch.object(b, "send_text") as paste, \
                            patch.object(b, "send_key") as key:
                        with self.assertRaises(b.DispatchUnconfirmed):
                            b.submit_text("offline-peer", "STATUS: new-token", marker="new-token")
                        paste.assert_not_called()
                        key.assert_not_called()

    def test_activity_and_queue_protection_survive_both_shapes(self):
        for position in ("inline", "final"):
            for status, tool in (("✻ Compacting conversation…", False),
                                 ("Messages to be submitted after completion:\n  queued payload", False),
                                 ("", True)):
                with self.subTest(position=position, status=status, tool=tool):
                    value = frame(goal_position=position, status=status, tool=tool)
                    self.assertTrue(b.compose_block_is_empty(value))
                    self.assertTrue(b._queued_or_active_input(value))
                    with NativeFixture(provider="claude") as native:
                        with self.assertRaises(b.DispatchUnconfirmed):
                            b.require_clearable_agent_input(value, "offline-peer")
                        native.send.assert_not_called()
                        native.key.assert_not_called()

    def test_malformed_or_partial_provider_fields_fail_closed(self):
        for provider in ("", "linxi/other", "lin xi", "linxi:", "linxi@", "a" * 65):
            with self.subTest(provider=provider):
                self.assert_unparsed(frame(provider=provider))
        self.assert_unparsed(frame().replace(MODEL, MODEL[:-1]))
        self.assert_unparsed(frame().replace(MODEL, MODEL + " extra"))

    def test_goal_is_only_one_complete_final_footer_row(self):
        value = frame(goal_position="final")
        for malformed in ("◎ /goal active (unknown)", GOAL + " extra", "◎ /goal paused (59m)"):
            with self.subTest(goal=malformed):
                self.assert_unparsed(value.replace(GOAL, malformed))
        self.assert_unparsed(value + GOAL + "\n")
        self.assert_unparsed(value + "unexpected footer text\n")
        self.assert_unparsed(value.replace("  ⏵⏵", GOAL + "\n  ⏵⏵"))
        self.assert_unparsed(value.replace("  ⏵⏵ bypass permissions on (shift+tab to cycle)\n", ""))

    def test_missing_or_mismatched_borders_fail_closed(self):
        value = frame(goal_position="final")
        for border in ("", "─" * 99):
            with self.subTest(border=border):
                self.assert_unparsed(value.replace("─" * 100, border, 1))


if __name__ == "__main__":
    unittest.main()
