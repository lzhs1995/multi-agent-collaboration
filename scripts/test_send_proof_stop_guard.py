#!/usr/bin/env python3
"""The Stop hook must block an unsent payload -- and nothing else.

Every blocking case is paired with a non-blocking control that differs in
exactly one fact, so a guard that always blocks (or never does) fails.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cmux_send_proof_stop_guard as guard  # noqa: E402

CALLER = "7063B683-2147-4850-BF71-AF48C937BD37"
TARGET = "41F454EB-0DDD-42BD-A529-BC79589F6DD5"
MARKER = "EXECUTOR_READY_test1"
PAYLOAD = MARKER + " asking for the next task pack"
SHA = "f" * 64

IN_COMPOSE = "\n".join([
    "• Ran some earlier tool",
    "",
    "› " + PAYLOAD,
    "",
    "  GPT-6-Astra xhigh · Context 29% used",
])
EMPTY = "\n".join([
    "• Ran some earlier tool",
    "",
    "› Ask Codex to do anything",
    "",
    "  GPT-6-Astra xhigh · Context 29% used",
])
QUEUED = "\n".join([
    "• Working (1h 07m · esc to interrupt)",
    "",
    "• Queued follow-up inputs",
    "  ↳ " + PAYLOAD,
    "    shift+← edit last queued message",
    "",
    "› Ask Codex to do anything",
    "",
    "  GPT-6-Astra xhigh · Context 29% used",
])


class FakeBridge:
    """Only the three predicates the guard is allowed to consult."""

    def __init__(self, screen):
        self.screen = screen

    def read_screen(self, surface, lines=200):
        return self.screen

    def compose_contains(self, screen, marker):
        rows = screen.splitlines()
        live = next((i for i in range(len(rows) - 1, -1, -1)
                     if rows[i].lstrip().startswith(("›", "❯"))), len(rows))
        block = rows[live:live + 1]
        return "".join(marker.split()) in "".join("".join(block).split())

    def pending_queue_lane(self, screen, marker):
        rows = screen.splitlines()
        live = next((i for i in range(len(rows) - 1, -1, -1)
                     if rows[i].lstrip().startswith(("›", "❯"))), len(rows))
        above = "\n".join(rows[:live])
        if "Queued follow-up inputs" in above and marker in above:
            return "followup_turn_end"
        if "Messages to be submitted after" in above and marker in above:
            return "steer_next_tool_boundary"
        return None


class StopGuardFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="stopguard-"))
        self.patches = [
            patch.object(guard, "JOURNAL_ROOT", self.root),
            patch.object(guard, "STATE", self.root / "state"),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in self.patches])

    def attempt(self, *, phases=("PASTE_INTENT", "ENTER_SENT"), caller=CALLER,
                receipt=False, marker=MARKER, confirmed=False, age=10.0,
                journal="message-dispatch-v1", sha=SHA):
        folder = self.root / journal / ("key-" + marker)
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time()
        events = [{"phase": p, "at_epoch": now - age} for p in phases]
        body = {"binding": {"marker": marker, "payload_sha256": sha,
                            "identity": {"caller_surface_uuid": caller,
                                         "target_surface_uuid": TARGET}},
                "phase": "CONFIRMED" if confirmed else (phases[-1] if phases else "PREPARED"),
                "events": events}
        path = folder / "attempt-0001.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        if receipt:
            (folder / "receipt.json").write_text("{}", encoding="utf-8")
        return path

    def run_guard(self, screen=IN_COMPOSE, *, proven=None, blocks_used=0,
                  caller=CALLER):
        bridge = FakeBridge(screen)
        with patch.object(guard, "native_proof", return_value=proven):
            return guard.evaluate({}, bridge=bridge, caller=caller,
                                  blocks_used=blocks_used)


class BlocksOnlyOnEvidence(StopGuardFixture):
    def test_stranded_without_native_proof_blocks(self):
        """THE CASE THIS EXISTS FOR."""
        self.attempt()
        result = self.run_guard(proven={"proven": False, "state": "NOT_RECEIVED"})
        self.assertEqual(result["decision"], "block")
        self.assertIn("Enter ≠ 送达", result["reason"])
        self.assertIn(MARKER, result["reason"])
        self.assertIn("--recover-stranded", result["reason"])

    def test_native_proof_allows(self):
        """Same screen, proof present: the receiver has it. Do not block."""
        self.attempt()
        result = self.run_guard(proven={"proven": True, "state": "RECEIVED"})
        self.assertEqual(result["decision"], "allow")
        self.assertEqual([f["verdict"] for f in result["findings"]], ["NATIVE_PROVEN"])

    def test_received_altered_allows(self):
        self.attempt()
        result = self.run_guard(proven={"proven": True, "state": "RECEIVED_ALTERED"})
        self.assertEqual(result["decision"], "allow")

    def test_empty_composer_allows(self):
        """Nothing in compose: nothing to block on, proof or not."""
        self.attempt()
        result = self.run_guard(screen=EMPTY,
                                proven={"proven": False, "state": "NOT_RECEIVED"})
        self.assertEqual(result["decision"], "allow")
        self.assertEqual([f["verdict"] for f in result["findings"]], ["NOT_IN_COMPOSE"])

    def test_tab_queued_payload_is_reported_not_blocked(self):
        """A real receiver queue will land on its own; blocking would trap us.

        The Tab lane defers to turn end (measured: 38 minutes). The payload has
        left our composer and the receiver owns it, so the honest verdict is
        NOT_IN_COMPOSE -- reported, not blocked. Blocking here could hold the
        session for the receiver's entire turn.
        """
        self.attempt()
        result = self.run_guard(screen=QUEUED,
                                proven={"proven": False, "state": "NOT_RECEIVED"})
        self.assertEqual(result["decision"], "allow")
        self.assertEqual([f["verdict"] for f in result["findings"]], ["NOT_IN_COMPOSE"])

    def test_queued_banner_with_payload_still_in_composer_blocks(self):
        """The measured false positive: a FOREIGN banner above our own draft.

        The old predicate called this "queued at receiver" -- the one state
        meaning "wait, do not resend" -- and the payload sat unsent for 18
        minutes. Our marker is in the live composer, so this must block.
        """
        self.attempt()
        screen = "\n".join([
            "• Working (1h 07m · esc to interrupt)",
            "",
            "• Messages to be submitted after next tool call",
            "  ↳ someone else's earlier message",
            "",
            "› " + PAYLOAD,
            "",
            "  GPT-6-Astra xhigh · Context 29% used",
        ])
        result = self.run_guard(screen=screen,
                                proven={"proven": False, "state": "NOT_RECEIVED"})
        self.assertEqual(result["decision"], "block")
        self.assertEqual([f["verdict"] for f in result["findings"]],
                         ["STRANDED_UNPROVEN"])

    def test_unmeasured_does_not_block(self):
        """No reader / no answer is NOT evidence of failure."""
        self.attempt()
        result = self.run_guard(proven=None)
        self.assertEqual(result["decision"], "allow")
        self.assertEqual([f["verdict"] for f in result["findings"]], ["UNMEASURED"])

    def test_settled_receipt_is_ignored(self):
        self.attempt(receipt=True)
        self.assertEqual(self.run_guard(proven={"proven": False})["decision"], "allow")

    def test_confirmed_attempt_is_ignored(self):
        self.attempt(confirmed=True)
        self.assertEqual(self.run_guard(proven={"proven": False})["decision"], "allow")

    def test_never_submitted_is_not_stranded(self):
        """No ENTER_SENT: the payload was never submitted, so not stranded."""
        self.attempt(phases=("PASTE_INTENT",))
        self.assertEqual(self.run_guard(proven={"proven": False})["decision"], "allow")

    def test_another_senders_attempt_is_not_mine_to_judge(self):
        self.attempt(caller="FD51FB34-5C79-4013-BD6B-9E10CFC743CD")
        result = self.run_guard(proven={"proven": False})
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["findings"], [])

    def test_no_attempts_allows(self):
        self.assertEqual(self.run_guard(proven={"proven": False})["decision"], "allow")

    def test_unreadable_target_does_not_block(self):
        self.attempt()
        bridge = FakeBridge(IN_COMPOSE)

        def boom(surface, lines=200):
            raise OSError("surface gone")

        with patch.object(guard, "native_proof", return_value={"proven": False}):
            result = guard.evaluate({}, bridge=bridge, reader=boom, caller=CALLER,
                                    blocks_used=0)
        self.assertEqual(result["decision"], "allow")
        self.assertEqual([f["verdict"] for f in result["findings"]], ["UNREADABLE"])

    def test_missing_bridge_allows(self):
        self.attempt()
        with patch.object(guard, "_bridge", return_value=None):
            result = guard.evaluate({}, caller=CALLER)
        self.assertEqual(result["decision"], "allow")


class BudgetCannotTrapTheSession(StopGuardFixture):
    def test_block_count_budget_releases(self):
        self.attempt()
        proven = {"proven": False, "state": "NOT_RECEIVED"}
        self.assertEqual(self.run_guard(proven=proven, blocks_used=0)["decision"],
                         "block")
        exhausted = self.run_guard(proven=proven, blocks_used=guard.MAX_BLOCKS)
        self.assertEqual(exhausted["decision"], "allow")
        self.assertEqual(exhausted["exhausted"], "count")

    def test_age_budget_releases(self):
        self.attempt(age=guard.MAX_AGE_SECONDS + 60)
        folder = self.root / "message-dispatch-v1" / ("key-" + MARKER)
        path = folder / "attempt-0001.json"
        import os
        old = time.time() - guard.MAX_AGE_SECONDS - 60
        os.utime(path, (old, old))
        result = self.run_guard(proven={"proven": False, "state": "NOT_RECEIVED"})
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["exhausted"], "age")

    def test_window_excludes_ancient_attempts(self):
        self.attempt()
        import os
        folder = self.root / "message-dispatch-v1" / ("key-" + MARKER)
        old = time.time() - guard.WINDOW_SECONDS - 600
        os.utime(folder / "attempt-0001.json", (old, old))
        result = self.run_guard(proven={"proven": False})
        self.assertEqual(result["findings"], [])


class HookContract(StopGuardFixture):
    def _main(self, payload, proven, env=None):
        import io
        import os
        out, err = io.StringIO(), io.StringIO()
        bridge = FakeBridge(IN_COMPOSE)
        with patch.object(guard, "native_proof", return_value=proven), \
             patch.object(guard, "_bridge", return_value=bridge), \
             patch.dict(os.environ, {"CMUX_SURFACE_ID": CALLER, **(env or {})}), \
             patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
             patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            code = guard.main([])
        return code, out.getvalue(), err.getvalue()

    def test_block_emits_decision_json_and_exits_zero(self):
        self.attempt()
        code, out, _err = self._main({"session_id": "s1"},
                                     {"proven": False, "state": "NOT_RECEIVED"})
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertEqual(body["decision"], "block")
        self.assertIn(MARKER, body["reason"])

    def test_allow_emits_nothing_on_stdout(self):
        self.attempt()
        code, out, _err = self._main({"session_id": "s2"},
                                     {"proven": True, "state": "RECEIVED"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_stop_hook_active_short_circuits(self):
        self.attempt()
        code, out, _err = self._main({"session_id": "s3", "stop_hook_active": True},
                                     {"proven": False, "state": "NOT_RECEIVED"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "", "a re-entered Stop hook must be able to finish")

    def test_disable_switch(self):
        self.attempt()
        code, out, _err = self._main({"session_id": "s4"},
                                     {"proven": False, "state": "NOT_RECEIVED"},
                                     env={"CMUX_SEND_PROOF_STOP_DISABLE": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_block_counter_persists_and_resets(self):
        self.attempt()
        unproven = {"proven": False, "state": "NOT_RECEIVED"}
        for expected in (1, 2, 3):
            self._main({"session_id": "counted"}, unproven)
            self.assertEqual(guard._load_blocks("counted", time.time()), expected)
        self._main({"session_id": "counted"}, {"proven": True, "state": "RECEIVED"})
        self.assertEqual(guard._load_blocks("counted", time.time()), 0,
                         "a proven delivery must reset the budget")

    def test_internal_error_fails_open_and_says_so(self):
        self.attempt()
        import io
        import os
        err = io.StringIO()
        with patch.object(guard, "evaluate", side_effect=RuntimeError("boom")), \
             patch.dict(os.environ, {"CMUX_SURFACE_ID": CALLER}), \
             patch.object(sys, "stdin", io.StringIO("{}")), \
             patch.object(sys, "stderr", err):
            code = guard.main([])
        self.assertEqual(code, 0)
        self.assertIn("INTERNAL_ERROR", err.getvalue())

    def test_garbage_stdin_fails_open(self):
        import io
        import os
        with patch.dict(os.environ, {"CMUX_SURFACE_ID": CALLER}), \
             patch.object(sys, "stdin", io.StringIO("not json at all")), \
             patch.object(guard, "_bridge", return_value=None), \
             patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(guard.main([]), 0)


class QueuedAdviceInStopGuard(unittest.TestCase):
    """Stop guard: 判决分对了不代表建议发对。

    _decide 正确区分 QUEUED_UNPROVEN / STRANDED_UNPROVEN，但 _render 原先只按
    journal 分支，于是排队态也印出 --recover-stranded —— 补键会重复投递。
    """

    def _item(self, verdict):
        return {"verdict": verdict, "journal": "message-dispatch-v1",
                "marker": "aaaabbbbccccdddd", "target": "surface:40",
                "age_seconds": 42, "attempt": "/tmp/attempt-0001.json",
                "lane": "followup"}

    def test_queued_is_told_to_wait_not_to_press_a_key(self):
        text = guard._render([self._item("QUEUED_UNPROVEN")], 0)
        self.assertIn("只能等", text)
        self.assertNotIn("--recover-stranded", text)

    def test_stranded_still_gets_the_recovery_key(self):
        """负控：若上面的分支过宽，真卡住的消息就永远没人救。"""
        text = guard._render([self._item("STRANDED_UNPROVEN")], 0)
        self.assertIn("--recover-stranded", text)
        self.assertNotIn("只能等", text)

    def test_both_verdicts_still_block_the_turn(self):
        """两种都不得被当成已送达而放行结束回合。"""
        import time as _time
        now = _time.time()
        for verdict in ("QUEUED_UNPROVEN", "STRANDED_UNPROVEN"):
            decision = guard._decide([self._item(verdict)], now, 0)
            self.assertEqual(decision["decision"], "block", verdict)


if __name__ == "__main__":
    unittest.main(verbosity=1)
