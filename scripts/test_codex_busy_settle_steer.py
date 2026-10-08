"""Settle-then-steer on the measured busy Codex composer.

Screens come from `verification/fixtures/codex-busy-*-20261008.txt`, captured
from a real busy Codex TUI (surface:42, 2026-10-08) with task payloads and
paths replaced by synthetic examples. Two measured properties drive this
module:

* A long paste renders in row batches. An Enter pressed while rows are still
  arriving is absorbed by the renderer (3 of 5 rows after 1.6 s).
* Codex word-wraps the composer and a soft boundary consumes exactly one
  payload space (row 5 of the full fixture); never more than one.

Tab on that same state only queues until the receiver's turn ends, which a
goal hook leaves unbounded (72 min measured). A settled Enter steers instead.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

import cmux_bridge as b

FIXTURES = Path(__file__).resolve().parent.parent / "verification" / "fixtures"
PAYLOAD = (FIXTURES / "codex-busy-payload-20261008.txt").read_text()
PARTIAL = (FIXTURES / "codex-busy-paste-partial-20261008.txt").read_text()
FULL = (FIXTURES / "codex-busy-wordwrap-full-20261008.txt").read_text()
MARKER = "READY_4545_123456"
FULL_ROWS = FULL.split("\n")
ECHO_ROWS = FULL_ROWS[3:8]
FOOTER_ROWS = FULL_ROWS[9:11]

# Idle and consumed screens are composed from the measured footer and echo
# rows; they are regression inputs, not independent live delivery evidence.
IDLE = "\n".join(["› Ask Codex to do anything", " "] + FOOTER_ROWS[:1])
CONSUMED = "\n".join(
    [FULL_ROWS[0], " "] + ECHO_ROWS + ["• Ran example check", IDLE])


class FixtureOwnershipTests(unittest.TestCase):
    def test_wordwrapped_full_payload_is_owned(self):
        self.assertEqual(b.receiver_input_kind(FULL), "AGENT_TUI")
        self.assertTrue(b._exact_pending_text(FULL, PAYLOAD))
        self.assertTrue(b._codex_tab_queue_allowed(FULL, PAYLOAD))

    def test_boundary_may_consume_one_space_but_never_two(self):
        # Row 5 is where the measured renderer dropped a payload space.
        two = FULL.replace(FULL_ROWS[5], FULL_ROWS[5].rstrip() + "  ", 1)
        self.assertNotEqual(two, FULL)
        self.assertFalse(b._exact_pending_text(two, PAYLOAD))

    def test_partial_render_is_not_payload_ownership(self):
        self.assertFalse(b._exact_pending_text(PARTIAL, PAYLOAD))
        self.assertFalse(b._codex_tab_queue_allowed(PARTIAL, PAYLOAD))

    def test_foreign_row_inside_the_wrapped_block_is_rejected(self):
        foreign = FULL.replace(FULL_ROWS[6], "  please keep my own note", 1)
        self.assertFalse(b._exact_pending_text(foreign, PAYLOAD))
        self.assertFalse(b._codex_tab_queue_allowed(foreign, PAYLOAD))


class StillRenderingPredicateTests(unittest.TestCase):
    def test_growing_prefix_only(self):
        self.assertTrue(b._still_rendering_own_paste(PARTIAL, PAYLOAD))
        self.assertFalse(b._still_rendering_own_paste(FULL, PAYLOAD))
        self.assertFalse(b._still_rendering_own_paste(IDLE, PAYLOAD))

    def test_foreign_prefix_is_not_our_paste(self):
        foreign = PARTIAL.replace(FULL_ROWS[3], "› someone else is typing", 1)
        self.assertFalse(b._still_rendering_own_paste(foreign, PAYLOAD))

    def test_non_agent_receiver_is_never_rendering(self):
        self.assertFalse(b._still_rendering_own_paste("$ ", PAYLOAD))


class BoundedEnvTests(unittest.TestCase):
    def test_clamps_parses_and_falls_back(self):
        with patch.dict(b.os.environ, {"X_T": "999"}):
            self.assertEqual(b._bounded_env("X_T", 8, 0, 40, int), 40)
        with patch.dict(b.os.environ, {"X_T": "-5"}):
            self.assertEqual(b._bounded_env("X_T", 8, 0, 40, int), 0)
        with patch.dict(b.os.environ, {"X_T": "nan"}):
            self.assertEqual(b._bounded_env("X_T", 0.5, 0.0, 2.0), 0.5)
        with patch.dict(b.os.environ, {"X_T": "abc"}):
            self.assertEqual(b._bounded_env("X_T", 8, 0, 40, int), 8)
        self.assertEqual(b._bounded_env("X_UNSET_T", 3.0, 0.0, 10.0), 3.0)


class SubmitSettleSteerTests(unittest.TestCase):
    def submit(self, screens, env=None, reads=None):
        """Run one submission over an exact screen sequence; return the keys.

        ``reads`` asserts how many screens the code consumed, so a wait that
        silently stops polling cannot pass by reaching the same verdict.
        """
        with patch.dict(b.os.environ, env or {}), \
                patch.object(b, "read_screen", side_effect=screens) as read, \
                patch.object(b, "send_text") as send, \
                patch.object(b, "send_key") as key, \
                patch.object(b.time, "sleep"):
            try:
                result = b._submit_text_once("peer", PAYLOAD, marker=MARKER)
            except b.DispatchUnconfirmed as exc:
                result = exc
            send.assert_called_once_with("peer", PAYLOAD)
            if reads is not None:
                self.assertEqual(read.call_count, reads)
            return result, [c.args for c in key.call_args_list]

    def test_settle_then_steer_confirms_delivery(self):
        result, keys = self.submit([IDLE, PARTIAL, FULL, CONSUMED])
        self.assertIsInstance(result, dict)
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["retries"], 1)
        self.assertEqual(keys, [("peer", "enter"), ("peer", "enter")])

    def test_settle_that_never_completes_spends_no_second_key(self):
        result, keys = self.submit(
            [IDLE, PARTIAL, PARTIAL], env={"CMUX_AGENT_SETTLE_POLLS": "1"},
            reads=3)
        self.assertIsInstance(result, b.DispatchUnconfirmed)
        self.assertEqual(result.state, b.COMPOSE_OCCUPIED)
        self.assertEqual(keys, [("peer", "enter")])

    def test_foreign_text_arriving_during_settle_is_preserved(self):
        foreign = FULL.replace(FULL_ROWS[6], "  please keep my own note", 1)
        result, keys = self.submit([IDLE, PARTIAL, foreign], reads=3)
        self.assertIsInstance(result, b.DispatchUnconfirmed)
        self.assertEqual(result.state, b.COMPOSE_OCCUPIED)
        self.assertEqual(keys, [("peer", "enter")])

    def test_compaction_over_our_draft_is_waited_out_then_steered(self):
        compacting = FULL.replace(
            FULL_ROWS[0], "• Compacting context (34s • esc to interrupt)", 1)
        result, keys = self.submit([IDLE, compacting, FULL, CONSUMED])
        self.assertIsInstance(result, dict)
        self.assertTrue(result["confirmed"])
        self.assertEqual(keys, [("peer", "enter"), ("peer", "enter")])

    def test_persistent_compaction_never_presses_a_key(self):
        compacting = FULL.replace(
            FULL_ROWS[0], "• Compacting context (34s • esc to interrupt)", 1)
        result, keys = self.submit(
            [IDLE, compacting, compacting],
            env={"CMUX_AGENT_COMPACTION_POLLS": "1"}, reads=3)
        self.assertIsInstance(result, b.DispatchUnconfirmed)
        # 合并 C2596 后：Enter 已发、原稿仍在接收端草稿区 = STRANDED（不是“已排队”）
        self.assertEqual(result.state, b.STRANDED_IN_COMPOSE)
        self.assertEqual(keys, [("peer", "enter")])

    def test_steer_landing_in_the_queue_is_reported_as_queued(self):
        queued = "\n".join(
            ["Messages to be submitted after next tool call"]
            + [r[2:] if i else r[2:] for i, r in enumerate(ECHO_ROWS)]
            + [IDLE])
        result, keys = self.submit([IDLE, FULL, queued])
        self.assertIsInstance(result, b.DispatchUnconfirmed)
        self.assertEqual(result.state, b.DELIVERY_QUEUED_AT_RECEIVER)
        self.assertEqual(keys, [("peer", "enter"), ("peer", "enter")])

    def test_interrupted_settle_leaves_a_resumable_journal_prefix(self):
        # The read-only waits add no phase, so an attempt interrupted during
        # them still satisfies resume_queue_only's prefix condition: exactly
        # one PASTE_INTENT and ENTER_INTENT, a POST_ENTER_OBSERVATION, no TAB
        # or EXTRA_ENTER. See references/executor-closeout-enforcement.md.
        class Interrupt(BaseException):
            pass

        phases = []
        reads = {"n": 0}

        def reader(*_a, **_k):
            reads["n"] += 1
            if reads["n"] == 1:
                return IDLE
            if reads["n"] >= 4:
                raise Interrupt("interrupted mid-settle")
            return PARTIAL

        with patch.object(b, "read_screen", side_effect=reader), \
                patch.object(b, "send_text"), patch.object(b, "send_key"), \
                patch.object(b.time, "sleep"):
            with self.assertRaises(Interrupt):
                b._submit_text_once(
                    "peer", PAYLOAD, marker=MARKER,
                    delivery_observer=lambda p, screen=None: phases.append(p))
        self.assertEqual(phases.count("PASTE_INTENT"), 1)
        self.assertEqual(phases.count("ENTER_INTENT"), 1)
        self.assertIn("POST_ENTER_OBSERVATION", phases)
        self.assertFalse(
            [p for p in phases if "TAB" in p or p == "EXTRA_ENTER_INTENT"])

    def test_settle_and_compaction_waits_record_no_observation(self):
        phases = []
        with patch.object(b, "read_screen",
                          side_effect=[IDLE, PARTIAL, FULL, CONSUMED]), \
                patch.object(b, "send_text"), patch.object(b, "send_key"), \
                patch.object(b.time, "sleep"):
            b._submit_text_once(
                "peer", PAYLOAD, marker=MARKER,
                delivery_observer=lambda phase, screen=None: phases.append(phase))
        # executor_closeout and resume validators count these exactly.
        self.assertEqual(phases.count("PASTE_INTENT"), 1)
        self.assertEqual(phases.count("ENTER_INTENT"), 1)
        self.assertEqual(phases.count("POST_ENTER_OBSERVATION"), 2)
        self.assertEqual(phases.count("EXTRA_ENTER_INTENT"), 1)
        self.assertNotIn("QUEUE_TAB_INTENT", phases)


if __name__ == "__main__":
    unittest.main()
