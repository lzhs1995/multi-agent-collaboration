#!/usr/bin/env python3
"""PreToolUse: end post-delivery work on the currently armed executor task."""
import json
import cmux_hook_identity as hook_identity
import sys
from cmux_consensus_stop_guard import (
    _active_markers, _has_active_markers, _workspace_key, _surface_key,
)
from executor_closeout import terminal_report, handoff_line
from cmux_callback_queue_resume import allowed as queue_resume_allowed


def _evaluate_resolved(payload):
    if payload.get('hook_event_name') != 'PreToolUse':
        return True, ''
    surface = _surface_key(payload)
    for marker in _active_markers(payload):
        evidence = terminal_report(marker, _workspace_key(payload), surface)
        if evidence:
            if queue_resume_allowed(payload, marker, evidence):
                continue
            return False, (
                'EXECUTOR_CLOSEOUT: report frozen; original callback attempt returned. '
                'Do not add tests, memories, watchers, retries, or other tool calls. '
                'End this turn now. The supervisor owns receipt reconciliation and '
                'acceptance/disarm before another task. This is not product acceptance. '
                'This task boundary is not evidence of an API failure. Do not ask the '
                'user to relay status to the already-bound supervisor. After verified '
                'disarm, authorized follow-up uses current receipts and disposition, '
                'not an old recap; this is not a permanent session stop. '
                'If delivery is not independently confirmed, use exactly:\n'
                + handoff_line(evidence))
    return True, ''


def evaluate(payload):
    if payload.get('hook_event_name') != 'PreToolUse':
        return True, ''
    try:
        if not _has_active_markers():
            return True, ''
        with hook_identity.evaluation(payload):
            return _evaluate_resolved(payload)
    except hook_identity.ERRORS as exc:
        return False, 'HOOK_CALLER_UNRESOLVED: ' + str(exc)


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
