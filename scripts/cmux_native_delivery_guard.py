#!/usr/bin/env python3
"""PostToolUse guard — Enter ≠ 发送。唯一判据是接收端原生会话记录。

用户 2026-10-04 / 10-08 三次实测同一缺陷：executor 的 prompt / callback 按了
Enter，却作为换行留在接收端 compose 框里。既有 guard 只读屏幕形状——繁忙接收端
上形状判据会猜错，而「我按过 Enter」从来不是送达。

本 guard 不看屏幕：对本 caller 刚发出的每一条投递，去接收端自己的 transcript
里找那条**整条相等**的 user 记录。找到才叫送达；找不到就 exit 2 回灌恢复指令。

只读：从不发送文本或按键、从不写回执、从不重贴 payload。

退出码：
  0  通过 / 跳过 / 仪器不可用（已显式说明）
  2  有投递未在接收端原生记录中出现，把判据与下一步回灌给模型

环境开关：
  CMUX_NATIVE_PROOF_DISABLE=1    完全停用
  CMUX_NATIVE_PROOF_ADVISORY=1   降级为建议（始终 exit 0，仍打印）
  CMUX_NATIVE_PROOF_WAIT=N       每条等待原生记录的秒数，默认 20（上限 120）
  CMUX_NATIVE_PROOF_WINDOW=N     只看最近 N 秒内的 attempt，默认 900
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cmux_native_delivery import (
    NOT_RECEIVED,
    RECEIVED,
    RECEIVED_ALTERED,
    wait_for_native_user_record,
)
from cmux_submit_confirmation_guard import (
    _extract_command,
    _extract_commands,
    looks_like_delivery,
)

_STATE_ROOT = Path(os.environ.get("HOME", "/tmp")) / ".local/state/multi-agent-collaboration"
_PROOF_DIR = _STATE_ROOT / "native-delivery-proof-v1"

# 消息与任务包各自的 journal。callback 的 attempt 落在任务目录下，不在这里。
_JOURNALS = ("message-dispatch-v1", "task-dispatch-v1")

# 从未向终端输入过的相位。接收端 compacting 时 bridge 会在第一次改动前就拒绝粘贴
# （NO_INPUT），这种 attempt 没有「已按过的 Enter」可补。集成方 2026-10-08 17:55Z
# 实测：我原先把它报成 NOT_RECEIVED 并建议 --recover-stranded，而那条路按构造不可用。
_NEVER_INPUT = frozenset({"PREPARED", "NO_INPUT"})

# 排队待消费 ≠ 卡在 compose。两者都是 NOT_RECEIVED，但处置相反：排队只能等，补键
# 会造成重复投递。2026-10-08 18:2xZ 实测：我自己发给 supervisor 的消息落在接收端
# 「Queued follow-up inputs」区，guard 却建议 --recover-stranded。
_QUEUED_STATE = "DELIVERY_QUEUED_AT_RECEIVER"


# 旧 attempt 没有结构化字段，只有发送器写进 error 的散文。探针必须抄自发送器源码
# （cmux_bridge.py 的 DispatchUnconfirmed 文案），不能照我自己以为的常量名去搜：
# 实测真实 error 串里根本没有 "DELIVERY_QUEUED_AT_RECEIVER" 这几个字，
# 于是我原先的兼容分支对所有真实旧 attempt 恒为假。
_QUEUED_PROSE = "delivery queued at receiver"


def _queued_at_receiver(attempt: dict[str, Any]) -> bool:
    """只认结构化字段，其次兼容旧 attempt 的 error 散文。"""
    state = attempt.get("delivery_state")
    if isinstance(state, str) and state:
        return state == _QUEUED_STATE
    return _QUEUED_PROSE in str(attempt.get("error") or "").lower()


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(os.environ[name])))
    except (KeyError, TypeError, ValueError):
        return default


def _callback_attempts(payload: dict[str, Any]) -> list[Path]:
    """本次命令里点名的 task pack 对应的 callback attempt 目录。

    callback 的 attempt 由发送器写在任务包自己的
    completion-callback-receipt-attempts/ 下，所以必须从命令里的 pack 路径反查，
    不能扫全局 journal。
    """
    out: list[Path] = []
    from cmux_submission_inputs import delivery_calls

    for source in _extract_commands(payload):
        for call in delivery_calls(source):
            pack = call.get("pack")
            if call.get("kind") != "callback" or not pack:
                continue
            try:
                folder = Path(pack).resolve().parent / "completion-callback-receipt-attempts"
            except (OSError, ValueError):
                continue
            if folder.is_dir():
                out.extend(sorted(folder.glob("attempt-*.json")))
    return out


def _binding_identity(attempt: dict[str, Any]) -> dict[str, Any]:
    binding = attempt.get("binding")
    binding = binding if isinstance(binding, dict) else {}
    identity = binding.get("identity")
    return binding, identity if isinstance(identity, dict) else {}


def _attempt_started(attempt: dict[str, Any], path: Path) -> float | None:
    started = attempt.get("started_at_epoch")
    if isinstance(started, (int, float)):
        return float(started)
    events = attempt.get("events")
    if isinstance(events, list):
        stamps = [e.get("at_epoch") for e in events
                  if isinstance(e, dict) and isinstance(e.get("at_epoch"), (int, float))]
        if stamps:
            return float(min(stamps))
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def pending_deliveries(payload: dict[str, Any], caller: str | None,
                       window_seconds: float = 900.0,
                       now: float | None = None,
                       state_root: Path | None = None) -> list[dict[str, Any]]:
    """本 caller 在窗口内发出、尚未证明送达的投递（消息 / 任务包 / 回调）。

    不看命令长相，只认自己 journal 里的 attempt：形状判据漏判过自定义 marker。
    """
    now = time.time() if now is None else now
    root = state_root if state_root is not None else _STATE_ROOT
    paths: list[tuple[str, Path]] = []
    for folder in _JOURNALS:
        for path in (root / folder).glob("*/attempt-*.json"):
            paths.append((folder, path))
    for path in _callback_attempts(payload):
        paths.append(("completion-callback", path))

    out: list[dict[str, Any]] = []
    for folder, path in paths:
        try:
            if now - path.stat().st_mtime > window_seconds:
                continue
            attempt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(attempt, dict):
            continue
        # 没输入过就没有可补的键，也没有「卡在 compose」的 payload。
        if attempt.get("phase") in _NEVER_INPUT:
            continue
        binding, identity = _binding_identity(attempt)
        own = identity.get("caller_surface_uuid")
        if caller and isinstance(own, str) and own.upper() != caller.upper():
            continue
        marker = binding.get("marker") or binding.get("completion_nonce")
        if not isinstance(marker, str) or not marker:
            continue
        started = _attempt_started(attempt, path)
        if started is None or now - started > window_seconds:
            continue
        out.append({
            "journal": folder,
            "attempt": str(path),
            "marker": marker,
            "queued_at_receiver": _queued_at_receiver(attempt),
            "since_epoch": started,
            "payload_sha256": binding.get("payload_sha256"),
            "text": binding.get("completion_callback"),
            "task_id": binding.get("task_id"),
            "target": identity.get("target_surface_uuid"),
        })
    return out


def verify(items: list[dict[str, Any]], wait_seconds: float = 20.0,
           home: str | None = None, waiter=None) -> list[dict[str, Any]]:
    """对每条投递查原生记录。没有判据可查的记为 UNVERIFIABLE，不算通过。"""
    waiter = waiter or wait_for_native_user_record
    results: list[dict[str, Any]] = []
    for item in items:
        text, sha = item.get("text"), item.get("payload_sha256")
        if not isinstance(text, str) or not text:
            text = None
        if not isinstance(sha, str) or not sha:
            sha = None
        if text is None and sha is None:
            # 整条相等是唯一判据；没有原文也没有 sha 就无从证明，不得当通过。
            results.append(dict(item, state="UNVERIFIABLE",
                                reason="attempt has neither payload_sha256 nor callback text"))
            continue
        try:
            proof = waiter(marker=item["marker"], since_epoch=item["since_epoch"],
                           text=text, payload_sha256=sha,
                           wait_seconds=wait_seconds, home=home)
        except (OSError, ValueError) as exc:
            results.append(dict(item, state="UNVERIFIABLE", reason=str(exc)))
            continue
        results.append(dict(item, **proof))
    return results


def _record(results: list[dict[str, Any]]) -> None:
    try:
        _PROOF_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = _PROOF_DIR / f"proof-{stamp}-{os.getpid()}.json"
        path.write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "results": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


_RECOVERY = {
    "message-dispatch-v1":
        "恢复（同一 attempt、只补一键、不重贴）：cmux_bridge.py submit-text "
        "--surface <同一目标> --text <原文> --marker <原 marker> --recover-stranded，"
        "然后用 cmux_native_delivery.py 复查原生记录。",
    "task-dispatch-v1":
        "任务包：不得重贴、不得改 nonce。由原发送器只读核收；仍未出现则报 BLOCKED 给主管。",
    "completion-callback":
        "完成回调：submit_completion_callback(pack, resume_queue_only=True) 只补一次 Tab；"
        "不得重发、不得手写 receipt。",
}


def _render(results: list[dict[str, Any]]) -> str:
    bad = [r for r in results if r.get("state") != RECEIVED]
    lines = [
        "",
        "[multi-agent 铁律] Enter ≠ 发送：以下投递未在接收端原生会话记录中出现。",
        "  按下 Enter 只是把键送到接收端；粘贴途中到达的 Enter 会被 compose 框当换行吃掉。",
        "  送达的唯一判据 = 接收端自己的 transcript 里出现**整条相等**的 user 记录。",
        "",
    ]
    for item in bad:
        lines.append(f"  {item.get('state')}  marker={item['marker']}  target={item.get('target')}")
        lines.append(f"    原 attempt = {item['attempt']}")
        if item.get("task_id"):
            lines.append(f"    task_id    = {item['task_id']}")
        if item.get("reason"):
            lines.append(f"    原因       = {item['reason']}")
        if item.get("state") == RECEIVED_ALTERED:
            lines.append("    接收端记录与原文空白不一致：payload 被改写，不得当成功投递。")
        if item.get("queued_at_receiver"):
            # 补键会造成重复投递，和卡 compose 的处置正好相反。
            lines.append("    DELIVERY_QUEUED_AT_RECEIVER：payload 在接收端排队区等它的 tool "
                         "boundary，不在 compose 里。")
            lines.append("    只能等：不要补键、不要重贴。稍后用下面的只读命令复查原生记录。")
        else:
            lines.append("    " + _RECOVERY.get(item["journal"], _RECOVERY["task-dispatch-v1"]))
    lines += [
        "",
        "  复查命令（只读，可直接跑）：",
        "    python3 scripts/cmux_native_delivery.py --marker <marker> "
        "--since-epoch <attempt 的 started_at_epoch> --payload-sha256 <binding.payload_sha256> --wait 30",
        "",
        "  禁止：重贴 payload、换新 nonce 重发、手写 receipt、把退出码或 ACK 当送达、",
        "        把「屏幕上看到 nonce」或「compose 为空」当送达。",
        "  接收端繁忙时消息会先进排队区，稍等再复查；排队不等于送达，也不许因此重发。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if os.environ.get("CMUX_NATIVE_PROOF_DISABLE") == "1":
        return 0
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        command = _extract_command(payload)
        if not command or not looks_like_delivery(command):
            return 0
        items = pending_deliveries(payload, os.environ.get("CMUX_SURFACE_ID"),
                                   window_seconds=_env_float("CMUX_NATIVE_PROOF_WINDOW",
                                                             900.0, 60.0, 7200.0))
        if not items:
            return 0
        results = verify(items, wait_seconds=_env_float("CMUX_NATIVE_PROOF_WAIT",
                                                        20.0, 0.0, 120.0))
    except Exception as exc:  # noqa: BLE001 - guard 自身故障不该打断用户工作
        sys.stderr.write(
            f"\n[multi-agent 铁律] 原生送达核验未完成（guard 自身异常）：{exc}\n"
            "本次投递的送达状态属未量到；请手动跑 cmux_native_delivery.py 复判。\n\n")
        return 0

    _record(results)
    if all(r.get("state") == RECEIVED for r in results):
        for r in results:
            sys.stderr.write(
                f"[multi-agent 原生送达] {r['marker']} RECEIVED: "
                f"{r.get('transcript')} @ {r.get('timestamp')}\n")
        return 0
    sys.stderr.write(_render(results))
    if os.environ.get("CMUX_NATIVE_PROOF_ADVISORY") == "1":
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
