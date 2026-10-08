#!/usr/bin/env python3
"""送达闸门：没有接收端原生记录，任何发送器都不得写 CONFIRMED。

2026-10-08 用户第四次报同一缺陷。前三次我都改在「检测」那一侧——hook 事后告警、
屏幕判据更严。但发送器自己仍然只凭屏幕就把 attempt 标成 CONFIRMED 并落 receipt，
于是「已发送」照旧是猜的。这个模块把判据搬到唯一真相上：接收端自己的 transcript
里出现整条相等的 user 记录。

闸门在**写 CONFIRMED 之前**调用。未证明送达时：
  - 允许恢复的场合（普通消息、首次 Enter 后）→ 返回 STRANDED，由发送器用既有的
    单键恢复预算补一次，然后必须再过一次闸门；
  - 其余场合 → 抛 DispatchUnconfirmed，绝不落 receipt。

永远不发文本、不发按键、不写回执。只读 transcript。

开关（故意做成显式、可审计）：
  CMUX_NATIVE_GATE=off        停用闸门（离线测试夹具用；生产用等于自废判据）
  CMUX_NATIVE_GATE_WAIT=N     每次等待原生记录的秒数，默认 25（上限 180）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cmux_native_delivery import (
    NOT_RECEIVED,
    RECEIVED,
    RECEIVED_ALTERED,
    wait_for_native_user_record,
)

GATE_OFF = "off"


def enabled() -> bool:
    return os.environ.get("CMUX_NATIVE_GATE", "on").strip().lower() != GATE_OFF


def wait_seconds() -> float:
    try:
        return max(0.0, min(180.0, float(os.environ["CMUX_NATIVE_GATE_WAIT"])))
    except (KeyError, TypeError, ValueError):
        return 25.0


def proof(marker, text, since_epoch, *, payload_sha256=None, waiter=None, home=None):
    """接收端原生记录的判决。闸门停用时显式标注，不伪装成已证明。"""
    if not enabled():
        return {"state": "GATE_DISABLED",
                "note": "CMUX_NATIVE_GATE=off: delivery was NOT proven"}
    waiter = waiter or wait_for_native_user_record
    return waiter(marker=marker, since_epoch=since_epoch, text=text,
                  payload_sha256=payload_sha256, wait_seconds=wait_seconds(), home=home)


def require(bridge, marker, text, since_epoch, *, payload_sha256=None, recoverable=False,
            waiter=None, home=None):
    """返回 (verdict, evidence)。verdict ∈ CONFIRMED / STRANDED。

    STRANDED 只在 recoverable=True 时返回，表示「可以用既有单键预算补一次」。
    其余未证明一律抛 DispatchUnconfirmed——闸门不放行未量到的送达。
    """
    evidence = proof(marker, text, since_epoch, payload_sha256=payload_sha256,
                     waiter=waiter, home=home)
    state = evidence.get("state")
    if state in (RECEIVED, "GATE_DISABLED"):
        return "CONFIRMED", evidence
    if state == RECEIVED_ALTERED:
        # 接收端记录到的不是我们发的那串字符：改写过的 payload 不算送达。
        raise bridge.DispatchUnconfirmed(
            f"NATIVE_DELIVERY_ALTERED marker={marker}: receiver recorded different "
            f"whitespace; do not treat as delivered ({evidence.get('transcript')})")
    if state == NOT_RECEIVED and recoverable:
        return "STRANDED", evidence
    raise bridge.DispatchUnconfirmed(
        f"NATIVE_DELIVERY_NOT_RECEIVED marker={marker}: Enter was accepted but no "
        f"whole-text user record exists in the receiver transcript; payload is still "
        f"in the composer. Do not repaste and do not mint a new nonce; recover the "
        f"original attempt with one key, then re-verify.")


def stamp(evidence, since_epoch):
    """落进 attempt/receipt 的证据块。读数随方法与时刻一起落盘。"""
    return {
        "criterion": "whole_text_user_record_in_receiver_native_transcript",
        "gate": "on" if enabled() else GATE_OFF,
        "since_epoch": since_epoch,
        "verified_at_epoch": time.time(),
        "evidence": evidence,
    }
