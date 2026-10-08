#!/usr/bin/env python3
"""Enter ≠ 发送：pre-Enter settle、原生送达判据、PostToolUse guard。

每条正向用例都配一个必须翻转的负控：期望值与观测值同形时「通过」不等于覆盖。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import cmux_bridge
import cmux_native_delivery as nd
import cmux_native_delivery_guard as guard

MARKER = "12c8bedace1645cfa7d8af4f662f817e"
TEXT = f"DONE|task-x|{MARKER}|REPORT=/tmp/r.md"


def _codex(text, ts="2026-10-08T14:00:00.000Z"):
    return {"type": "response_item", "timestamp": ts,
            "payload": {"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": text}]}}


def _claude(text, ts="2026-10-08T14:00:00.000Z", **extra):
    return dict({"type": "user", "timestamp": ts,
                 "message": {"role": "user", "content": text}}, **extra)


def _queued(text, ts="2026-10-08T14:00:00.000Z"):
    return {"type": "attachment", "timestamp": ts,
            "attachment": {"type": "queued_command", "prompt": text}}


class SettleBeforeEnter(unittest.TestCase):
    """实测根因：Enter 在粘贴突发期间到达，被 compose 当换行吃掉。"""

    def _run(self, screens, text=TEXT, **env):
        reads = []

        def read_screen(surface, lines=200):
            reads.append(surface)
            return screens[min(len(reads) - 1, len(screens) - 1)]

        with patch.dict(os.environ, env, clear=False), \
                patch.object(cmux_bridge, "read_screen", read_screen), \
                patch.object(cmux_bridge.time, "sleep", lambda s: None):
            used = cmux_bridge._settle_paste_before_enter("surface:40", text)
        return used, reads

    def test_waits_while_compose_still_changing(self):
        growing = [f"❯ {TEXT[:n]}" for n in (10, 20, 30, len(TEXT))]
        stable = f"❯ {TEXT}"
        used, reads = self._run(growing + [stable, stable])
        self.assertGreaterEqual(used, 2, "settle 必须在 compose 还在变时继续等")
        self.assertTrue(reads, "settle 必须真的读屏，而不是空等")

    def test_returns_promptly_once_stable(self):
        stable = f"❯ {TEXT}"
        used, _ = self._run([stable, stable, stable])
        self.assertLessEqual(used, 3, "compose 稳定后不该耗尽预算")

    def test_budget_zero_disables_and_reads_nothing(self):
        """负控：预算为 0 时必须一次不读——否则上面的断言可能恒真。"""
        used, reads = self._run([f"❯ {TEXT}"], CMUX_AGENT_PASTE_SETTLE_READS="0")
        self.assertEqual(used, 0)
        self.assertEqual(reads, [], "预算 0 仍读屏 = 开关失效")

    def test_bounded_when_compose_never_settles(self):
        churn = [f"❯ {TEXT[:n]}" for n in range(1, 40)]
        used, _ = self._run(churn, CMUX_AGENT_PASTE_SETTLE_READS="4")
        self.assertEqual(used, 4, "settle 必须有界，不能永久阻塞发送器")

    def test_settle_runs_before_enter_in_submit(self):
        """接线断言：settle 必须发生在 ENTER_INTENT 之前，否则等于没等。"""
        order = []
        idle = "❯ \ntab to queue message"
        with patch.object(cmux_bridge, "read_screen", lambda *a, **k: idle), \
                patch.object(cmux_bridge, "require_agent_input", lambda *a, **k: None), \
                patch.object(cmux_bridge, "send_text",
                             lambda *a, **k: order.append("PASTE")), \
                patch.object(cmux_bridge, "send_key",
                             lambda *a, **k: order.append("ENTER")), \
                patch.object(cmux_bridge, "_settle_paste_before_enter",
                             lambda *a, **k: order.append("SETTLE") or 1), \
                patch.object(cmux_bridge.time, "sleep", lambda s: None):
            try:
                cmux_bridge._submit_text_once("surface:40", TEXT)
            except cmux_bridge.DispatchUnconfirmed:
                # 送达核验失败无妨：本用例只断言三个动作的先后顺序。
                pass
        self.assertEqual(order[:3], ["PASTE", "SETTLE", "ENTER"],
                         f"settle 必须夹在粘贴与 Enter 之间，实测顺序 {order}")


class UserTurnText(unittest.TestCase):
    def test_codex_and_claude_and_queued_user_turns(self):
        self.assertEqual(nd.user_turn_text(_codex(TEXT)), TEXT)
        self.assertEqual(nd.user_turn_text(_claude(TEXT)), TEXT)
        self.assertEqual(nd.user_turn_text(_queued(TEXT)), TEXT,
                         "接收端繁忙时消息以 queued_command 落盘，必须认")

    def test_non_user_records_are_not_turns(self):
        """负控：这些若被当成 user 记录，assistant 回显就能伪装成送达。"""
        for event in (
            {"type": "assistant", "message": {"role": "assistant", "content": TEXT}},
            _claude(TEXT, isMeta=True),
            _claude(TEXT, isSidechain=True),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": TEXT}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                  "content": [{"type": "input_text",
                                                               "text": TEXT}]}},
            {"type": "attachment", "attachment": {"type": "hook_success", "prompt": TEXT}},
        ):
            self.assertIsNone(nd.user_turn_text(event), f"不该把它当 user 记录：{event}")


class NativeProofCriterion(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.day = self.home / ".codex/sessions/2026/10/08"
        self.day.mkdir(parents=True)
        self.since = nd._epoch("2026-10-08T13:59:00.000Z")

    def _write(self, *events, name="rollout-a.jsonl"):
        path = self.day / name
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                        encoding="utf-8")
        os.utime(path, (time.time(), time.time()))
        return path

    def _find(self, **kw):
        kw.setdefault("marker", MARKER)
        kw.setdefault("since_epoch", self.since)
        return nd.find_native_user_record(home=str(self.home), **kw)

    def test_received_by_exact_text(self):
        self._write(_codex(TEXT))
        self.assertEqual(self._find(text=TEXT)["state"], nd.RECEIVED)

    def test_received_by_payload_sha(self):
        self._write(_codex(TEXT))
        self.assertEqual(self._find(payload_sha256=nd._sha(TEXT))["state"], nd.RECEIVED)

    def test_quoted_marker_inside_longer_message_is_not_received(self):
        """实测 14:28Z：用户把 r23 协议行粘进抱怨里。子串绝不算送达。"""
        self._write(_codex(f"你发的这条又卡在 compose 了：{TEXT} 赶紧修"))
        self.assertEqual(self._find(text=TEXT)["state"], nd.NOT_RECEIVED)

    def test_record_before_attempt_is_not_received(self):
        self._write(_codex(TEXT, ts="2026-10-08T12:00:00.000Z"))
        self.assertEqual(self._find(text=TEXT)["state"], nd.NOT_RECEIVED,
                         "attempt 之前的旧记录不能当本次送达")

    def test_altered_whitespace_is_flagged_not_passed(self):
        self._write(_codex(TEXT.replace("|", " | ")))
        result = self._find(text=TEXT)
        self.assertEqual(result["state"], nd.RECEIVED_ALTERED)

    def test_empty_transcripts_are_not_received(self):
        self.assertEqual(self._find(text=TEXT)["state"], nd.NOT_RECEIVED)

    def test_cli_exit_codes(self):
        self._write(_codex(TEXT))
        text_file = self.home / "payload.txt"
        text_file.write_text(TEXT, encoding="utf-8")
        with patch.object(Path, "home", staticmethod(lambda: self.home)):
            ok = nd.main(["--marker", MARKER, "--since-epoch", str(self.since),
                          "--text-file", str(text_file)])
            missing = nd.main(["--marker", "deadbeef" * 4, "--since-epoch", str(self.since),
                               "--text-file", str(text_file)])
        self.assertEqual((ok, missing), (0, 3))


class GuardEnforcement(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        folder = self.root / "message-dispatch-v1" / "abc"
        folder.mkdir(parents=True)
        self.attempt = folder / "attempt-0001.json"
        self.attempt.write_text(json.dumps({
            "phase": "POST_ENTER_OBSERVATION",
            "started_at_epoch": time.time() - 30,
            "binding": {"marker": MARKER, "payload_sha256": nd._sha(TEXT),
                        "identity": {"caller_surface_uuid": "AAAA", "target_surface_uuid": "BBBB"}},
        }), encoding="utf-8")
        self.payload = {"tool_input": {"command":
                        f"python3 cmux_bridge.py submit-text --surface surface:40 --marker {MARKER}"}}

    def _pending(self, caller="AAAA", **kw):
        return guard.pending_deliveries(self.payload, caller, state_root=self.root, **kw)

    def test_finds_own_recent_attempt(self):
        items = self._pending()
        self.assertEqual([i["marker"] for i in items], [MARKER])

    def test_ignores_other_callers_and_stale_attempts(self):
        """负控：若这两个过滤失效，guard 会对别人的投递乱报。"""
        self.assertEqual(self._pending(caller="ZZZZ"), [])
        self.assertEqual(self._pending(window_seconds=60.0, now=time.time() + 10_000), [])

    def test_exit_2_when_not_received(self):
        results = guard.verify(self._pending(), wait_seconds=0.0,
                               waiter=lambda **kw: {"state": nd.NOT_RECEIVED})
        self.assertEqual([r["state"] for r in results], [nd.NOT_RECEIVED])
        self.assertIn("Enter ≠ 发送", guard._render(results))
        self.assertIn("--recover-stranded", guard._render(results))

    def test_pass_when_received(self):
        results = guard.verify(self._pending(), wait_seconds=0.0,
                               waiter=lambda **kw: {"state": nd.RECEIVED,
                                                    "transcript": "/t.jsonl"})
        self.assertEqual([r["state"] for r in results], [nd.RECEIVED])

    def test_attempt_without_criterion_is_unverifiable_not_pass(self):
        self.attempt.write_text(json.dumps({
            "started_at_epoch": time.time() - 30,
            "binding": {"marker": MARKER,
                        "identity": {"caller_surface_uuid": "AAAA"}},
        }), encoding="utf-8")
        results = guard.verify(self._pending(), wait_seconds=0.0,
                               waiter=lambda **kw: {"state": nd.RECEIVED})
        self.assertEqual([r["state"] for r in results], ["UNVERIFIABLE"],
                         "没有判据可查时不得当通过")

    def test_subprocess_entry_passes_cleanly_on_non_delivery(self):
        """放行路径必须真走子进程入口，且 stderr 无内部错误（fail-open 会吞崩溃）。"""
        proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_DIR / "cmux_native_delivery_guard.py")],
            input=json.dumps({"tool_input": {"command": "ls -la"}}),
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, CMUX_NATIVE_PROOF_WAIT="0"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("guard 自身异常", proc.stderr)

    def test_subprocess_disable_switch(self):
        proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_DIR / "cmux_native_delivery_guard.py")],
            input=json.dumps(self.payload), capture_output=True, text=True, timeout=120,
            env=dict(os.environ, CMUX_NATIVE_PROOF_DISABLE="1"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.strip(), "")


class NativeGate(unittest.TestCase):
    """闸门：发送器不得在没有原生记录时写 CONFIRMED。

    这些用例自己管 CMUX_NATIVE_GATE；test_all.py 刻意不覆盖本类（否则被测开关
    被 mock 掉，断言空转）。
    """

    def setUp(self):
        import cmux_native_gate
        self.gate = cmux_native_gate
        self.bridge = cmux_bridge
        self.env = patch.dict(os.environ, {"CMUX_NATIVE_GATE": "on",
                                           "CMUX_NATIVE_GATE_WAIT": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_received_confirms(self):
        verdict, evidence = self.gate.require(
            self.bridge, MARKER, TEXT, 0,
            waiter=lambda **kw: {"state": nd.RECEIVED, "transcript": "/t.jsonl"})
        self.assertEqual(verdict, "CONFIRMED")
        self.assertEqual(evidence["state"], nd.RECEIVED)

    def test_not_received_raises_when_not_recoverable(self):
        with self.assertRaises(self.bridge.DispatchUnconfirmed) as ctx:
            self.gate.require(self.bridge, MARKER, TEXT, 0, recoverable=False,
                              waiter=lambda **kw: {"state": nd.NOT_RECEIVED})
        self.assertIn("NATIVE_DELIVERY_NOT_RECEIVED", str(ctx.exception))

    def test_not_received_is_strandable_when_recovery_is_allowed(self):
        verdict, _ = self.gate.require(
            self.bridge, MARKER, TEXT, 0, recoverable=True,
            waiter=lambda **kw: {"state": nd.NOT_RECEIVED})
        self.assertEqual(verdict, "STRANDED")

    def test_altered_payload_never_confirms(self):
        with self.assertRaises(self.bridge.DispatchUnconfirmed):
            self.gate.require(self.bridge, MARKER, TEXT, 0, recoverable=True,
                              waiter=lambda **kw: {"state": nd.RECEIVED_ALTERED,
                                                   "transcript": "/t.jsonl"})

    def test_gate_off_is_labelled_not_proven(self):
        with patch.dict(os.environ, {"CMUX_NATIVE_GATE": "off"}):
            verdict, evidence = self.gate.require(
                self.bridge, MARKER, TEXT, 0,
                waiter=lambda **kw: self.fail("gate off must not query transcripts"))
            # stamp 必须在同一个 patch 作用域内读，否则读到的是恢复后的开关值。
            self.assertEqual(self.gate.stamp(evidence, 0)["gate"], "off")
        self.assertEqual(verdict, "CONFIRMED")
        self.assertEqual(evidence["state"], "GATE_DISABLED")
        self.assertIn("NOT proven", evidence["note"])
        self.assertEqual(self.gate.stamp(evidence, 0)["gate"], "on",
                         "开关恢复后 stamp 必须随之变化，否则它没在读环境")

    def test_stamp_records_criterion_and_time(self):
        stamp = self.gate.stamp({"state": nd.RECEIVED}, 123.0)
        self.assertEqual(stamp["criterion"],
                         "whole_text_user_record_in_receiver_native_transcript")
        self.assertEqual(stamp["since_epoch"], 123.0)
        self.assertGreater(stamp["verified_at_epoch"], 0)

    def test_message_sender_refuses_receipt_without_native_proof(self):
        """端到端：屏幕说已消费、但原生记录没有 → 不得落 receipt。"""
        import cmux_message_journal
        home = Path(tempfile.mkdtemp())
        marker, text = "ff00ff00ff00ff00", "hello ff00ff00ff00ff00"
        screen = "❯ \ntab to queue message"
        fake = types.SimpleNamespace(
            TaskPackContractError=self.bridge.TaskPackContractError,
            DispatchUnconfirmed=self.bridge.DispatchUnconfirmed,
            _looks_like_task_dispatch=lambda t: False,
            pin_workspace=lambda s: {"workspace_uuid": "W", "caller_surface_uuid": "C",
                                     "target_surface_uuid": "T", "target_pane_uuid": "P"},
            screen_hash=self.bridge.screen_hash,
            read_screen=lambda s, lines=200: screen,
            _delivery_confirmed=lambda *a, **k: True,
            recover_stranded_once=lambda *a, **k: {"recovered": "key"},
            _submit_text_once=lambda *a, **k: (
                k["delivery_observer"]("PASTE_INTENT", screen),
                k["delivery_observer"]("ENTER_SENT"),
                {"confirmed": True})[-1],
        )
        with patch.object(Path, "home", staticmethod(lambda: home)), \
                patch.object(cmux_message_journal.time, "sleep", lambda s: None):
            with self.assertRaises(self.bridge.DispatchUnconfirmed):
                cmux_message_journal.deliver(fake, "surface:40", text, marker)
        receipts = list(home.rglob("receipt.json"))
        self.assertEqual(receipts, [], "未证明送达却落了 receipt")
        attempts = list(home.rglob("attempt-*.json"))
        self.assertTrue(attempts, "attempt 必须留痕")
        body = json.loads(attempts[-1].read_text())
        self.assertNotEqual(body.get("phase"), "CONFIRMED")
        self.assertIn("NATIVE_DELIVERY_NOT_RECEIVED", body.get("error", ""))


if __name__ == "__main__":
    unittest.main()
