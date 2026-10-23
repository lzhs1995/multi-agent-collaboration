"""Validate a supervisor mailbox candidate before writing it; never send or consume."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import executor_reply as replies
from cmux_evidence_io import snapshot

MAX_BYTES = 1024 * 1024


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError('NONSTANDARD_JSON_CONSTANT:' + value)


def read_object(path, expected=None):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("ABSOLUTE_PATH_REQUIRED")
    if expected is not None and (not isinstance(expected, str)
                                or not re.fullmatch('[0-9a-f]{64}', expected)):
        raise ValueError("EXACT_SHA256_REQUIRED")
    raw, identity = snapshot(path, limit=MAX_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        raise ValueError("PIN_MISMATCH")
    try:
        body = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_object,
                          parse_constant=_invalid_constant)
    except RecursionError as exc:
        raise ValueError('JSON_NESTING_LIMIT_EXCEEDED') from exc
    if not isinstance(body, dict):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return body, dict(path=str(path), bytes=len(raw), sha256=digest,
                      file_identity=list(identity))


def diagnose(state, body):
    """Observe the existing validation contract; no episode mutation."""
    import executor_reask as reask
    from executor_reask_lifecycle import valid_wait_contract

    facts = dict(schema="executor-reply-preflight-v1", input_operations=0,
                 state_changes=0, body_read_confirmed=False,
                 business_accepted=False, reply_consumed=False,
                 allowed_statuses=sorted(replies.REPLY_STATES))
    required = ("caller_surface_uuid", "supervisor", "task_id", "episode_id")
    if (not isinstance(state, dict) or any(not isinstance(state.get(k), str) for k in required)
            or any(not state[k] for k in required if k != 'task_id')):
        return dict(facts, outcome="REPLY_REFUSED", reason="ORIGINAL_EPISODE_BINDING_REQUIRED")
    if state.get("state") != reask.WAITING:
        return dict(facts, outcome="REPLY_REFUSED", reason="ORIGINAL_EPISODE_NOT_WAITING")
    marker = body.get("marker") if isinstance(body, dict) else None
    if not isinstance(body, dict):
        return dict(facts, outcome="REPLY_REFUSED", reason="REPLY_OBJECT_REQUIRED")
    if (not isinstance(marker, str) or not marker or len(marker) > 160
            or not re.fullmatch('[A-Za-z0-9_-]+', marker)):
        return dict(facts, outcome="REPLY_REFUSED", reason="REPLY_MARKER_INVALID")
    continuation = state.get('continuation')
    asks = state.get('asks', [])
    count = state.get('attempt_count', 0)
    closed = state.get('replies_closed_through', 0)
    secret = state.get('marker_secret')
    if (not isinstance(asks, list) or any(not isinstance(row, dict) for row in asks)
            or type(count) is not int or count < 0
            or type(closed) is not int or not 0 <= closed <= count
            or continuation is not None and not isinstance(continuation, dict)
            or secret is not None and (not isinstance(secret, str) or not secret)):
        return dict(facts, outcome="REPLY_REFUSED", reason="ORIGINAL_EPISODE_SHAPE_INVALID")
    try:
        # open_marker alone always accepts a noncontinuing marker. Prove its
        # issuance first, including late HMAC markers outside rolling history.
        issued = reask.issued_marker(state, marker) if secret else any(
            row.get('marker') == marker for row in asks)
        if continuation:
            if (not secret or continuation.get('enabled') is not True
                    or continuation.get('status') != 'ACTIVE'):
                return dict(facts, outcome="REPLY_REFUSED", reason="CONTINUING_AUTHORITY_INACTIVE")
            issued = issued and reask.open_marker(state, marker)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        issued = False
    if not issued:
        return dict(facts, outcome="REPLY_REFUSED", reason="UNISSUED_OR_CONSUMED_MARKER")
    reason = replies.rejection(
        body, [marker], caller=state["caller_surface_uuid"],
        task_id=state["task_id"], episode_id=state["episode_id"],
        supervisor=state["supervisor"],
    )
    if reason is None and state.get("continuation"):
        if not valid_wait_contract(body, state):
            reason = "CONTINUING_WAIT_OWNER_OR_DEADLINE_INVALID"
    if reason:
        result = dict(facts, outcome="REPLY_REFUSED", reason=reason)
        if reason == "REPLY_STATUS_UNSUPPORTED" and body.get("status") in ("STANDBY", "HOLD", "WAIT"):
            result["suggestion"] = (
                "Use status=WAITING_DEPENDENCY with a concrete trigger; continuing waits also need "
                "the original supervisor owner and finite 0<next_due_epoch<=deadline_epoch. "
                "Preserve the refused file and prepare a new candidate; do not mutate an old reply."
            )
        return result
    return dict(facts, outcome="REPLY_VALID_FOR_ORIGINAL_EPISODE",
                reason=None, original_controller_required=True,
                authority_verified=False,
                instruction="Structural validation only. The authenticated original supervisor must verify live authority before publication; executor FULL-read and exact consume remain separate.")


def preflight(state_file, state_sha256, body_file, body_sha256):
    state, state_pin = read_object(state_file, state_sha256)
    body, body_pin = read_object(body_file, body_sha256)
    result = diagnose(state, body)
    # Re-read both exact snapshots so a changing input cannot look validated.
    _, current_state = read_object(state_file, state_sha256)
    _, current_body = read_object(body_file, body_sha256)
    if current_state != state_pin or current_body != body_pin:
        raise ValueError("INPUT_IDENTITY_CHANGED")
    return dict(result, state=state_pin, candidate=body_pin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--state-sha256", required=True)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--body-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = preflight(args.state_file, args.state_sha256, args.body_file, args.body_sha256)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = dict(schema="executor-reply-preflight-v1", outcome="REPLY_REFUSED",
                      reason=str(exc)[:240], input_operations=0, state_changes=0,
                      reply_consumed=False, business_accepted=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "REPLY_VALID_FOR_ORIGINAL_EPISODE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
