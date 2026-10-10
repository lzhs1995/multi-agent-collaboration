#!/usr/bin/env python3
"""Observe workflow hook payloads without making ordinary turns fail.

The workspace and panel guards remain the transport boundary.  This entrypoint
is deliberately a one-way observability adapter for the workflow checks whose
old task gates could trap an otherwise repairable Codex/Claude session.
"""
import json
import sys

WORKFLOW_HOOKS = frozenset({
    'cmux_executor_closeout_guard.py',
    'cmux_handshake_receipt_guard.py',
    'cmux_consensus_round_guard.py',
    'cmux_lease_guard.py',
    'cmux_consensus_stop_guard.py',
    'cmux_executor_idle_guard.py',
    'cmux_executor_reask_stop_guard.py',
    'cmux_native_delivery_guard.py',
    'cmux_supervisor_report_guard.py',
})
MAX_INPUT_CHARS = 2 * 1024 * 1024


def hook_from_argv(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != '--hook':
        return None
    value = argv[1]
    return value if value in WORKFLOW_HOOKS else None


def main() -> int:
    # A hook must never turn an unreadable or stale payload into a session
    # block.  Keep stdin bounded by the host's normal hook payload limits.
    try:
        raw = sys.stdin.read(MAX_INPUT_CHARS + 1)
        if len(raw) > MAX_INPUT_CHARS:
            return 0
        payload = json.loads(raw) if raw.strip() else {}
        if isinstance(payload, dict):
            hook = hook_from_argv()
            if hook:
                import cmux_hook_runtime_audit as runtime_audit
                runtime_audit.record(
                    payload,
                    'ADVISORY_OBSERVED',
                    original_hook=hook,
                    mode='advisory',
                )
    except BaseException:
        # Advisory telemetry is never allowed to recreate the outage it
        # diagnoses.  Native delivery and explicit harness paths retain their
        # own strict checks and receipts.
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
