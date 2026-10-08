"""Enter is not delivery: a payload stranded in the Codex composer (2026-10-08).

Measured on surface:40: the bridge pasted during "• Compacting context", Enter
did nothing, and the payload stayed in the composer. Tab was then refused for
two reasons: Codex's word wrap dropped the space at "only|because", and a
finished compaction block in scrollback was read as the current state.
"""
import unittest
from unittest.mock import patch

import cmux_bridge as b

MARKER = "C2596_STATUS_20261008T131247Z"
WORDS = ("%s | status: r22 cmaverse-source-delivery-evidence-20261008-r22: bridge test "
         "B1_45ec8129 at 13:04:19Z was COMPOSE_OCCUPIED only because my turn "
         "was still running and my compose box was empty so please re-run the preflight or "
         "dispatch through the protected Tab queue; I am watching read-only and will reply "
         "with the exact acknowledgement as soon as the token arrives. This is a one-time "
         "status message, not a loop, and will not be resent." % MARKER)
# Measured geometry: the first row ends at "only" and "because" starts row two.
WIDTH = WORDS.index(" because") + 4
# Codex pads its footer to the terminal width (measured: content width + 2).
FOOTER = "  GPT-6-Astra xhigh · ~/project · Context 28% used".ljust(WIDTH + 2)


def codex_wrap(text, width=WIDTH):
    """Codex word wrap: break at a space and drop that space."""
    rows, row = [], ""
    for word in text.split(" "):
        if row and len(row) + 1 + len(word) > width:
            rows.append(row)
            row = word
        else:
            row = word if not row else row + " " + word
    rows.append(row)
    return ["› " + rows[0]] + ["  " + r for r in rows[1:]]


def screen(status, text=WORDS, scrollback=(), hint=True):
    rows = list(scrollback) + [status, " ", " "] + codex_wrap(text) + [" ", " ", FOOTER]
    if hint:
        rows.append("  tab to queue message")
    return "\n".join(rows)


WORKING = "• Working (21m 34s • esc to interrupt)"
COMPACTING = "• Compacting context (31s • esc to interrupt)"
OLD_COMPACTION = ["• Compacting context (1s • esc to interrupt)", "  └ Making room to continue.",
                  "• Ran rtk proxy cat file", "  └ ok"]


class WrapMatchTests(unittest.TestCase):
    def test_fixture_reproduces_space_dropping_wrap(self):
        rows = codex_wrap(WORDS)
        self.assertGreater(len(rows), 2)
        self.assertTrue(rows[0].endswith("only"))
        self.assertTrue(rows[1].startswith("  because"))

    def test_word_wrapped_payload_is_owned(self):
        self.assertTrue(b._exact_pending_text(screen(WORKING), WORDS))

    def test_changed_payloads_still_rejected(self):
        for changed in (WORDS + "x", WORDS.replace("only because", "only  because"),
                        WORDS.replace("was still", "was  still"), WORDS.replace("loop", "lop")):
            with self.subTest(changed=changed[-40:]):
                self.assertFalse(b._exact_pending_text(screen(WORKING), changed))

    def test_short_row_is_not_a_wrap(self):
        # A row far shorter than the composer cannot have word-wrapped.
        rendered = "\n".join([WORKING, " ", "› " + MARKER, "  short line", " ", " ",
                              "-" * WIDTH, FOOTER])
        self.assertFalse(b._exact_pending_text(rendered, MARKER + " short line"))
        # Same rows remain an exact match for the explicit newline payload.
        self.assertTrue(b._exact_pending_text(rendered.replace("\n" + "-" * WIDTH, ""),
                                              MARKER + "\nshort line"))


class StatusRegionTests(unittest.TestCase):
    def test_stale_compaction_in_scrollback_does_not_block_tab(self):
        s = screen(WORKING, scrollback=OLD_COMPACTION)
        self.assertFalse(b.receiver_cannot_submit_now(s))
        self.assertTrue(b._codex_tab_queue_allowed(s, WORDS))

    def test_current_compaction_blocks_tab(self):
        for status in (COMPACTING, "• Reconnecting... 2/5 (esc to interrupt)"):
            with self.subTest(status=status):
                s = screen(status, scrollback=OLD_COMPACTION)
                self.assertTrue(b.receiver_cannot_submit_now(s))
                self.assertFalse(b._codex_tab_queue_allowed(s, WORDS))


class PrePasteGateTests(unittest.TestCase):
    def test_no_paste_while_receiver_compacting(self):
        idle = "\n".join([COMPACTING, " ", "› ", FOOTER])
        with patch.object(b, "read_screen", return_value=idle), \
                patch.object(b, "send_text") as send, patch.object(b, "send_key") as key, \
                patch.object(b.time, "sleep"):
            with self.assertRaises(b.DispatchUnconfirmed) as caught:
                b._submit_text_once("peer", WORDS, marker=MARKER)
        self.assertIn("RECEIVER_COMPACTING", str(caught.exception))
        send.assert_not_called()
        key.assert_not_called()

    def test_enter_left_in_composer_is_named_stranded(self):
        idle = "\n".join([WORKING, " ", "› ", FOOTER])
        # Compaction starts between the pre-read and Enter.
        stuck = screen(COMPACTING)
        with patch.object(b, "read_screen", side_effect=[idle, stuck, stuck]), \
                patch.object(b, "send_text"), patch.object(b, "send_key") as key, \
                patch.object(b.time, "sleep"):
            with self.assertRaises(b.DispatchUnconfirmed) as caught:
                b._submit_text_once("peer", WORDS, marker=MARKER)
        self.assertEqual(caught.exception.state, b.STRANDED_IN_COMPOSE)
        self.assertIn("NOT delivered", str(caught.exception))
        self.assertEqual([c.args[1] for c in key.call_args_list], ["enter"])


class RecoverStrandedTests(unittest.TestCase):
    BEFORE = "\n".join([WORKING, " ", "› ", FOOTER])

    def consumed(self):
        return "\n".join(["› " + WORDS, "• Read evidence", WORKING, " ", "› ", FOOTER])

    def run_recovery(self, screens):
        events = []
        with patch.object(b, "read_screen", side_effect=screens), \
                patch.object(b, "send_text") as send, patch.object(b, "send_key") as key, \
                patch.object(b.time, "sleep"):
            try:
                result = b.recover_stranded_once("peer", WORDS, MARKER, self.BEFORE,
                                                 delivery_observer=lambda p, s=None: events.append(p),
                                                 wait_seconds=60)
            except b.DispatchUnconfirmed as exc:
                result = exc
        send.assert_not_called()  # never repaste
        return result, [c.args[1] for c in key.call_args_list], events

    def test_waits_out_compaction_then_one_tab(self):
        result, keys, events = self.run_recovery(
            [screen(COMPACTING), screen(COMPACTING), screen(WORKING, scrollback=OLD_COMPACTION),
             self.consumed()])
        self.assertEqual(keys, ["tab"])
        self.assertEqual(events, ["QUEUE_TAB_INTENT", "POST_QUEUE_TAB_OBSERVATION"])
        self.assertTrue(result["confirmed"])

    def test_idle_receiver_gets_one_enter(self):
        idle_pending = "\n".join([" ", " "] + codex_wrap(WORDS) + [" ", " ", FOOTER])
        result, keys, _ = self.run_recovery([idle_pending, self.consumed()])
        self.assertEqual(keys, ["enter"])
        self.assertTrue(result["confirmed"])

    def test_changed_draft_gets_no_key(self):
        result, keys, _ = self.run_recovery([screen(WORKING, text=WORDS + " extra")])
        self.assertEqual(keys, [])
        self.assertIsInstance(result, b.DispatchUnconfirmed)

    def test_unconsumed_after_key_is_not_success(self):
        s = screen(WORKING)
        result, keys, _ = self.run_recovery([s, s])
        self.assertEqual(keys, ["tab"])
        self.assertIsInstance(result, b.DispatchUnconfirmed)


if __name__ == "__main__":
    unittest.main()
