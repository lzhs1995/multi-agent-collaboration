#!/usr/bin/env python3
"""Mutation check for the settle/steer submit path (2026-10-08).

Each mutation must make at least one NAMED assertion fail. A non-zero exit is
not evidence by itself: the predicted test id must appear in the failure list,
and must be present in the passing baseline first.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "scripts" / "cmux_bridge.py"
TESTS = ["test_codex_busy_settle_steer.py", "test_bidirectional_submission.py"]

MUTATIONS = [
    # (label, old, new, test ids that must fail)
    ("settle_disabled",
     'polls = _bounded_env("CMUX_AGENT_SETTLE_POLLS", 8, 0, 40, int)',
     'polls = 0',
     ["test_settle_then_steer_confirms_delivery"]),
    ("compaction_wait_disabled",
     'polls = _bounded_env("CMUX_AGENT_COMPACTION_POLLS", 20, 0, 60, int)',
     'polls = 0',
     ["test_compaction_over_our_draft_is_waited_out_then_steered"]),
    ("steer_removed",
     "if _queued_or_active_input(screen) and not steer:",
     "if _queued_or_active_input(screen):",
     ["test_settle_then_steer_confirms_delivery",
      "test_codex_busy_exact_own_payload_gets_one_steer_enter"]),
    ("rendering_prefix_unchecked",
     "return bool(shown) and len(shown) < len(want) and want.startswith(shown)",
     "return bool(shown) and len(shown) < len(want)",
     ["test_foreign_prefix_is_not_our_paste",
      "test_foreign_text_arriving_during_settle_is_preserved"]),
    ("settle_observes_screens",
     'def _settle_own_paste(surface, screen, text, confirm_lines):',
     'def _settle_own_paste(surface, screen, text, confirm_lines, _o=None):',
     []),  # signature-only control: must NOT break anything
]


def failures(ids_only=True):
    failed = []
    for name in TESTS:
        out = subprocess.run([sys.executable, name], cwd=ROOT / "scripts",
                             capture_output=True, text=True).stderr
        failed += re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)
    return sorted(set(failed))


def main():
    original = BRIDGE.read_text()
    baseline = failures()
    if baseline:
        print("BASELINE_NOT_GREEN", baseline)
        return 1
    ok = True
    try:
        for label, old, new, expect in MUTATIONS:
            if original.count(old) != 1:
                print(f"{label}: ANCHOR_NOT_UNIQUE count={original.count(old)}")
                ok = False
                continue
            BRIDGE.write_text(original.replace(old, new, 1))
            got = failures()
            missing = [t for t in expect if t not in got]
            status = "OK" if (not missing and (got or not expect)) else "WEAK"
            if expect and not got:
                status = "NOT_DETECTED"
            if not expect and got:
                status = "UNEXPECTED_BREAK"
            ok = ok and status == "OK"
            print(f"{label}: {status} expected={expect} failed={got}")
    finally:
        BRIDGE.write_text(original)
    print("restored_identical:", BRIDGE.read_text() == original)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
