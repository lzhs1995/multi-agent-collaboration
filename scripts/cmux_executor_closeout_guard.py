#!/usr/bin/env python3
"""PreToolUse: end post-delivery work on the currently armed executor task."""
import json
import os
import sys
from cmux_consensus_stop_guard import _active_markers, _workspace_key
from executor_closeout import terminal_report, handoff_line


def evaluate(payload):
    if payload.get('hook_event_name') != 'PreToolUse':
        return True, ''
    surface = (os.environ.get('CMUX_SURFACE_ID') or payload.get('surface_id')
               or payload.get('surface_uuid'))
    for marker in _active_markers(payload):
        evidence = terminal_report(marker, _workspace_key(payload), surface)
        if evidence:
            return False, (
                'EXECUTOR_CLOSEOUT: report frozen; original callback attempt returned. '
                'Do not add tests, memories, watchers, retries, or other tool calls. '
                'End this turn now. The supervisor owns receipt reconciliation and '
                'acceptance/disarm before another task. This is not product acceptance. '
                'If delivery is not independently confirmed, use exactly:\n'
                + handoff_line(evidence))
    return True, ''


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
    except (ValueError, OSError):
        return 0
    ok, reason = evaluate(payload)
    if not ok:
        print(reason, file=sys.stderr)
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
