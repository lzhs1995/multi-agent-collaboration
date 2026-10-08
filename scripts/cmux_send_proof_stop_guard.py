#!/usr/bin/env python3
"""Stop hook: do not end a turn while my own payload sits unsent in a composer.

The failure this exists for (measured three times on 2026-10-08): the sender
pressed Enter, the receiver absorbed it into the still-rendering paste as a
NEWLINE, and the payload stayed in the receiver's composer. The sender reported
"queued at receiver" -- the one state meaning "wait, do not resend" -- then
ended its turn. Nothing was delivered. Only the user noticed, 18 and 38 minutes
later.

Enter is a keystroke. Delivery is a record in the receiver's own session log.
This hook blocks the turn from ending while BOTH hold:

  * our marker is still sitting in the receiver's live compose block, and
  * the receiver's session log has no user record containing our payload.

Native proof overrides the screen in both directions. The block is bounded by
count and by age, so a receiver that never settles cannot trap the session.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

STATE = Path.home() / ".local/state/multi-agent-collaboration/send-proof-stop-v1"
JOURNAL_ROOT = Path.home() / ".local/state/multi-agent-collaboration"
JOURNALS = ("message-dispatch-v1", "task-dispatch-v1", "callback-dispatch-v1")
MAX_BLOCKS = int(os.environ.get("CMUX_SEND_PROOF_STOP_MAX_BLOCKS", "8"))
MAX_AGE_SECONDS = float(os.environ.get("CMUX_SEND_PROOF_STOP_MAX_AGE", "1800"))
WINDOW_SECONDS = float(os.environ.get("CMUX_SEND_PROOF_STOP_WINDOW", "3600"))


def _bridge():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import cmux_bridge
        return cmux_bridge
    except Exception:      # noqa: BLE001 - a missing bridge must not block work
        return None


def native_proof(marker, payload_sha256, since):
    """Did the receiver record our payload as a user turn?

    Delegates to this release's native-record reader. Returns None when the
    question cannot be answered (no reader, no usable evidence), which is NOT
    the same as "not delivered" and never on its own justifies a block.
    """
    if not marker or not payload_sha256 or not isinstance(since, (int, float)):
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import cmux_native_delivery as native
    except Exception:      # noqa: BLE001 - a missing reader must not block work
        return None
    try:
        found = native.find_native_user_record(
            marker=marker, since_epoch=float(since), payload_sha256=payload_sha256)
    except Exception:      # noqa: BLE001
        return None
    if not isinstance(found, dict):
        return None
    state = found.get("state")
    if state == getattr(native, "RECEIVED", "RECEIVED"):
        return {"proven": True, "state": state, "record": found}
    if state == getattr(native, "RECEIVED_ALTERED", "RECEIVED_ALTERED"):
        # Same characters, whitespace mangled: it arrived. Do not block.
        return {"proven": True, "state": state, "record": found}
    if state == getattr(native, "NOT_RECEIVED", "NOT_RECEIVED"):
        return {"proven": False, "state": state}
    return None
def _attempts(caller, now):
    """Unsettled dispatch attempts owned by this caller, newest first."""
    out = []
    for folder in JOURNALS:
        root = JOURNAL_ROOT / folder
        if not root.is_dir():
            continue
        for path in root.glob("*/attempt-*.json"):
            try:
                stat = path.stat()
                if now - stat.st_mtime > WINDOW_SECONDS:
                    continue
                if (path.parent / "receipt.json").exists():
                    continue        # already settled by its own controller
                attempt = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(attempt, dict) or attempt.get("phase") == "CONFIRMED":
                continue
            binding = attempt.get("binding") or {}
            identity = binding.get("identity") or {}
            marker = binding.get("marker")
            target = identity.get("target_surface_uuid")
            mine = str(identity.get("caller_surface_uuid") or "").upper()
            if not marker or not target or not mine:
                continue
            if caller and mine != str(caller).upper():
                continue        # another sender's attempt is not mine to judge
            events = attempt.get("events") or []
            phases = [e.get("phase") for e in events if isinstance(e, dict)]
            if "ENTER_SENT" not in phases:
                continue        # nothing was ever submitted: not stranded
            pasted = [e.get("at_epoch") for e in events
                      if isinstance(e, dict) and e.get("phase") == "PASTE_INTENT"]
            since = min((t for t in pasted if isinstance(t, (int, float))),
                        default=stat.st_mtime)
            out.append({
                "journal": folder,
                "attempt": str(path),
                "marker": marker,
                "target": target,
                "payload_sha256": binding.get("payload_sha256"),
                "since_epoch": since,
                "age_seconds": max(0.0, now - stat.st_mtime),
                "mtime": stat.st_mtime,
                "native_already_recorded": bool(attempt.get("native_proof")
                                                or attempt.get("native_evidence")),
            })
    return sorted(out, key=lambda item: item["mtime"], reverse=True)


def evaluate(payload, *, bridge=None, reader=None, caller=None, now=None,
             blocks_used=None):
    """Decide whether this turn may end. Pure: no I/O beyond reads."""
    now = time.time() if now is None else now
    if caller is None:
        caller = os.environ.get("CMUX_SURFACE_ID")
    bridge = _bridge() if bridge is None else bridge
    if bridge is None:
        return {"decision": "allow", "reason": "bridge unavailable; not measured"}
    if reader is None:
        read_screen = getattr(bridge, "read_screen", None)
        if not callable(read_screen):
            return {"decision": "allow", "reason": "no reader; not measured"}

        def reader(surface, lines=200, _fn=read_screen):
            return _fn(surface, lines=lines)

    findings = []
    for item in _attempts(caller, now):
        try:
            screen = reader(item["target"])
        except Exception as exc:      # noqa: BLE001
            findings.append(dict(item, verdict="UNREADABLE", detail=str(exc)))
            continue
        # Still in the LIVE compose block? A marker elsewhere on screen (queued
        # banner, scrollback, our own quoted text) is a different state.
        in_compose = False
        try:
            in_compose = bool(bridge.compose_contains(screen, item["marker"]))
        except Exception:             # noqa: BLE001
            pass
        if not in_compose:
            findings.append(dict(item, verdict="NOT_IN_COMPOSE"))
            continue
        lane = None
        try:
            lane = bridge.pending_queue_lane(screen, item["marker"])
        except AttributeError:
            try:
                lane = ("unknown_queue"
                        if bridge.pending_queue_holds(screen, item["marker"]) else None)
            except Exception:         # noqa: BLE001
                lane = None
        except Exception:             # noqa: BLE001
            lane = None
        proof = native_proof(item["marker"], item.get("payload_sha256"),
                             item.get("since_epoch"))
        if proof and proof.get("proven"):
            findings.append(dict(item, verdict="NATIVE_PROVEN", lane=lane,
                                 native=proof))
            continue
        if lane:
            # Genuinely queued at the receiver AND not yet in its log: it will
            # land at the receiver's own boundary. Waiting is correct, but the
            # turn must not end pretending it was delivered.
            findings.append(dict(item, verdict="QUEUED_UNPROVEN", lane=lane))
            continue
        if proof is None:
            findings.append(dict(item, verdict="UNMEASURED", lane=lane))
            continue
        findings.append(dict(item, verdict="STRANDED_UNPROVEN", lane=lane))
    return _decide(findings, now, blocks_used)
def _decide(findings, now, blocks_used):
    """Block only on positive evidence, and only within a bounded budget."""
    blocking = [f for f in findings
                if f["verdict"] in ("STRANDED_UNPROVEN", "QUEUED_UNPROVEN")]
    result = {"findings": findings, "blocking": blocking}
    if not blocking:
        return dict(result, decision="allow",
                    reason="no unproven payload in a receiver composer")
    # Escape valves, so a receiver that never settles cannot trap the session.
    stale = [f for f in blocking if f["age_seconds"] > MAX_AGE_SECONDS]
    if stale and len(stale) == len(blocking):
        return dict(result, decision="allow", exhausted="age",
                    reason=("every stranded attempt is older than %ds; reporting "
                            "instead of blocking" % int(MAX_AGE_SECONDS)))
    if blocks_used is not None and blocks_used >= MAX_BLOCKS:
        return dict(result, decision="allow", exhausted="count",
                    reason=("stop-block budget %d exhausted; reporting instead of "
                            "blocking" % MAX_BLOCKS))
    return dict(result, decision="block", reason=_render(blocking, blocks_used))


def _render(blocking, blocks_used):
    lines = [
        "[multi-agent 铁律] Enter ≠ 送达：本回合有 payload 仍停在接收端 compose，"
        "且接收端自己的会话记录里没有这条消息。不得结束回合。",
        "",
    ]
    for item in blocking:
        lines.append("  marker=%s  target=%s  已过 %ds" %
                     (item["marker"], item["target"], int(item["age_seconds"])))
        lines.append("    attempt = %s" % item["attempt"])
        lines.append("    判据：marker 仍在 live compose 块内 + 原生 user 记录缺失 "
                     "(payload_sha256 未出现)。")
        if item.get("lane"):
            lines.append("    接收端队列车道 = %s（已排队但未进入其会话记录；"
                         "steer 走下一个工具边界，followup 要等整轮结束）。"
                         % item["lane"])
        if item.get("verdict") == "QUEUED_UNPROVEN":
            # 排队与卡 compose 处置相反：排队只能等，补键会重复投递。_decide 已把
            # 两种 verdict 分开了，但这里原先只按 journal 分支，于是排队态也照样
            # 印出补键命令 —— 判决分对了，建议仍然发错。
            lines.append("    只能等：不要补键、不要重贴。到接收端自己的边界它会落盘；"
                         "稍后用 cmux_native_delivery.py 只读复查。")
        elif item["journal"] == "message-dispatch-v1":
            lines.append("    恢复（同一 attempt、只补一键、绝不重贴）：")
            lines.append("      cmux_bridge.py submit-text --surface <同一目标> "
                         "--text <原文> --marker %s --recover-stranded" % item["marker"])
        else:
            lines.append("    任务包/回调：由原受保护发送器只读核收后补键，不得重贴。")
    lines += [
        "",
        "  只有以下两者之一才算送达：接收端会话记录里出现含本 payload 的 user 记录；",
        "  或原 attempt 的完整 payload 消费证据。返回码、空 compose、marker 回显、",
        "  「已排队」横幅都不是送达。",
        "  本 Hook 不代按键。预算：最多 %d 次拦停或 %d 分钟，之后只报告不拦停。" %
        (MAX_BLOCKS, int(MAX_AGE_SECONDS / 60)),
    ]
    if blocks_used:
        lines.append("  本回合已拦停 %d 次。" % blocks_used)
    return "\n".join(lines)


def _state_path(session_id):
    safe = "".join(ch for ch in str(session_id or "nosession")
                   if ch.isalnum() or ch in "-_")[:80]
    return STATE / ("%s.json" % (safe or "nosession"))


def _load_blocks(session_id, now):
    path = _state_path(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    if now - float(data.get("updated_at") or 0) > WINDOW_SECONDS:
        return 0
    try:
        return int(data.get("blocks") or 0)
    except (TypeError, ValueError):
        return 0


def _save_blocks(session_id, blocks, now, markers):
    path = _state_path(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps({"blocks": blocks, "updated_at": now,
                                    "markers": markers}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        pass


def main(argv=None):
    if os.environ.get("CMUX_SEND_PROOF_STOP_DISABLE") == "1":
        return 0
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # A Stop hook that already fed itself back must be able to finish.
    if payload.get("stop_hook_active") is True:
        return 0
    now = time.time()
    session = payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
    used = _load_blocks(session, now)
    try:
        result = evaluate(payload, now=now, blocks_used=used)
    except Exception as exc:      # noqa: BLE001 - fail open, but say so loudly
        sys.stderr.write("[multi-agent 铁律] send-proof stop guard INTERNAL_ERROR: "
                         "%s；送达状态未量到，请只读复判。\n" % exc)
        return 0
    if result.get("decision") != "block":
        if result.get("exhausted"):
            sys.stderr.write("[multi-agent 铁律] %s\n%s\n" %
                             (result.get("reason"),
                              _render(result.get("blocking") or [], used)))
        _save_blocks(session, 0, now, [])
        return 0
    _save_blocks(session, used + 1, now,
                 [f["marker"] for f in result.get("blocking") or []])
    print(json.dumps({"decision": "block", "reason": result["reason"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
