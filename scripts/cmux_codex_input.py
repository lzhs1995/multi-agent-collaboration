"""Direct input to the bound, already-loaded Codex thread; never terminal input.

An active turn uses turn/steer with its exact id; an idle thread uses turn/start
without changing any settings. One durable intent authorizes one RPC request.
An ambiguous return is observed, never retried or converted to paste/Tab/Enter.
Only the original native transcript can prove reception.
"""
import hashlib
import json
from pathlib import Path
import time
import uuid

import cmux_native_delivery as native
from cmux_codex_rpc import DaemonRPC

TRANSPORT = "codex-native-input-v1"
INPUT_PHASE = "NATIVE_INPUT_INTENT"


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _turn_id(value):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise native.NativeDeliveryError("CODEX_ACTIVE_TURN_INVALID")
    return value


def prepare(client, binding, text):
    """Bounded metadata only: never hydrate histories or resume another thread."""
    native._validate_binding(binding, text)
    if binding["provider"] != "codex":
        raise native.NativeDeliveryError("CODEX_NATIVE_PROVIDER_REQUIRED")
    thread_id = binding["session_id"]
    thread = client.request("thread/read", {"threadId": thread_id, "includeTurns": False})["thread"]
    if (thread.get("id") != thread_id or thread.get("path") != binding["transcript"]["path"]
            or thread.get("canAcceptDirectInput") is not True):
        raise native.NativeDeliveryError("CODEX_NATIVE_THREAD_NOT_ACCEPTING: no terminal fallback")
    # Re-read the latest turn instead of inferring activity from a TUI spinner.
    turns = client.request("thread/turns/list", {"threadId": thread_id, "limit": 1,
        "sortDirection": "desc", "itemsView": "notLoaded"})["data"]
    if not isinstance(turns, list) or len(turns) > 1:
        raise native.NativeDeliveryError("CODEX_NATIVE_TURNS_INVALID")
    status = thread.get("status", {}).get("type")
    params = {"threadId": thread_id, "input": [{"type": "text", "text": text}]}
    latest = turns[0] if turns else None
    if status == "active" and latest and latest.get("status") == "inProgress":
        method = "turn/steer"
        params["expectedTurnId"] = _turn_id(latest.get("id"))
    elif status == "idle" and (latest is None or latest.get("status") in
                               ("completed", "interrupted", "failed")):
        # The native API also steers if another turn starts in this narrow race.
        # It is still ONE same-thread request with no setting overrides.
        method = "turn/start"
    else:
        raise native.NativeDeliveryError("CODEX_NATIVE_TURN_CHANGED_OR_UNAVAILABLE: zero input")
    return dict(transport=TRANSPORT, method=method, params=params,
                socket=dict(client.socket_identity), observed_status=status,
                observed_turn_id=latest.get("id") if latest else None,
                observed_at_epoch=time.time())


def validate_request(value, binding, text):
    """No setting overrides, alternate thread, changed payload or unpinned peer."""
    if not isinstance(value, dict) or value.get("transport") != TRANSPORT:
        raise native.NativeDeliveryError("ORIGINAL_NATIVE_REQUEST_REQUIRED")
    method, params = value.get("method"), value.get("params")
    expected = {"threadId": binding["session_id"], "input": [{"type": "text", "text": text}]}
    if method == "turn/steer":
        expected["expectedTurnId"] = _turn_id(params.get("expectedTurnId")) if isinstance(params, dict) else None
    elif method != "turn/start":
        raise native.NativeDeliveryError("NATIVE_INPUT_METHOD_INVALID")
    if binding["provider"] != "codex" or params != expected:
        raise native.NativeDeliveryError("NATIVE_INPUT_REQUEST_CHANGED")
    import os
    peer = value.get("socket", {})
    if (not isinstance(peer, dict) or peer.get("uid") != os.geteuid()
            or peer.get("peer_uid") != os.geteuid() or peer.get("mode") != 0o600
            or type(peer.get("device")) is not int or type(peer.get("inode")) is not int
            or not isinstance(peer.get("path"), str) or not Path(peer["path"]).is_absolute()):
        raise native.NativeDeliveryError("NATIVE_INPUT_SOCKET_PROOF_REQUIRED")
    return hashlib.sha256(_canonical(value)).hexdigest()


def send_once(binding, text, *, recheck, emit, observe, wait_seconds=3):
    """Callers hold their existing journal locks and fsync emit() before input."""
    recheck()
    started = time.monotonic()
    outcome = "UNKNOWN"
    returned_turn = None
    journal_error = None
    with DaemonRPC(timeout=8) as client:
        request = prepare(client, binding, text)
        validate_request(request, binding, text)
        recheck()
        emit(INPUT_PHASE, evidence={"native_request": request})
        # A failed check after intent consumes the attempt too. No retroactive
        # NO_INPUT, even if the native mutation has not yet been attempted.
        recheck()
        try:
            result = client.request(request["method"], request["params"])
            returned_turn = (result.get("turnId") if request["method"] == "turn/steer"
                             else result.get("turn", {}).get("id"))
            _turn_id(returned_turn)
            if request["method"] == "turn/steer" and returned_turn != request["params"]["expectedTurnId"]:
                raise native.NativeDeliveryError("NATIVE_INPUT_RESPONSE_TURN_CHANGED")
            outcome = "RPC_ACCEPTED"
            diagnostic = {"method": request["method"], "turn_id": returned_turn,
                          "rpc_outcome": outcome}
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            # Even a timeout may have delivered input. No second mutation,
            # new nonce, key, or queue fallback is authorized by this error.
            outcome = "RPC_ERROR_OR_UNKNOWN"
            diagnostic = {"method": request["method"], "rpc_outcome": outcome,
                          "error_type": type(exc).__name__, "error": str(exc)[:1500]}
        # The durable original intent remains the authority. A diagnostic
        # write failure must neither retry input nor skip receipt observation.
        try:
            emit("NATIVE_INPUT_RESULT", evidence=diagnostic)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            journal_error = {"error_type": type(exc).__name__, "error": str(exc)[:1500]}
    deadline = time.monotonic() + max(0, min(float(wait_seconds), 10))
    while True:
        proof = observe()
        if proof.get("confirmed") is True or time.monotonic() >= deadline:
            return dict(proof, transport=TRANSPORT, native_method=request["method"],
                        native_rpc_outcome=outcome, native_turn_id=returned_turn,
                        native_result_journal_error=journal_error,
                        terminal_input_operations=0, input_operations=1,
                        submission_key=None, retries=0,
                        elapsed_ms=round((time.monotonic() - started) * 1000, 3))
        time.sleep(0.25)
