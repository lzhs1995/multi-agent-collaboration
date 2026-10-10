"""Native delivery for the explicitly authorized successor pair.

The ordinary native-delivery-v1 binding is intentionally untouched.  This
module carries both workspace identities and uses the successor bridge for
every target paste/key operation.
"""
from __future__ import annotations

import hashlib
import contextlib
import fcntl
import json
import os
import time
from pathlib import Path

import cmux_native_delivery as native
import successor_rebind
import cmux_prompt_reference as prompt_reference
from cmux_evidence_io import open_regular_lock, read_bytes


SCHEMA = "native-successor-delivery-v1"


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _target_row(bridge, target_uuid):
    tree = json.loads(bridge._run("tree", "--all", "--json", "--id-format", "both"))
    rows = []
    for window in tree.get("windows", []):
        for workspace in window.get("workspaces", []):
            for pane in workspace.get("panes", []):
                for surface in pane.get("surfaces", []):
                    if str(surface.get("id", "")).upper() == str(target_uuid).upper():
                        rows.append({"workspace_uuid": workspace.get("id"),
                                     "target_surface_uuid": surface.get("id"),
                                     "target_pane_uuid": pane.get("id"),
                                     "tty": surface.get("tty"),
                                     "kind": surface.get("type"),
                                     "dock": surface.get("dock_scope")})
    if len(rows) != 1 or rows[0]["kind"] != "terminal" or rows[0]["dock"] == "global" or not rows[0]["tty"]:
        raise successor_rebind.SuccessorRebindError("SUCCESSOR_REBIND_DENIED: target terminal changed")
    return rows[0]


def bind_target(bridge, artifact, text):
    if not isinstance(text, str) or not text:
        raise native.NativeDeliveryError("NATIVE_EMPTY_PAYLOAD")
    pair = bridge.pin_successor_pair(artifact)
    row = _target_row(bridge, pair["target_surface_uuid"])
    if (row["workspace_uuid"].upper() != pair["target_workspace_uuid"].upper()
            or row["target_pane_uuid"].upper() != pair["target_pane_uuid"].upper()):
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_TARGET_WORKSPACE_OR_PANE_CHANGED")
    process = native._target_process(row)
    provider = native._provider(process)
    session = native._live_session(process, provider)
    path = native._transcript_path(provider, session)
    binding = {
        "schema": SCHEMA,
        "binding_mode": artifact["binding_mode"],
        "artifact_sha256": _hash(_canonical(artifact)),
        "identity": {
            "caller_workspace_uuid": pair["caller_workspace_uuid"],
            "caller_surface_uuid": pair["caller_surface_uuid"],
            "caller_pane_uuid": pair["caller_pane_uuid"],
            "target_workspace_uuid": pair["target_workspace_uuid"],
            "target_surface_uuid": pair["target_surface_uuid"],
            "target_pane_uuid": pair["target_pane_uuid"],
        },
        "target_tty": row["tty"],
        "process": native._public_process(process),
        "provider": provider,
        "session_id": session,
        "transcript": native._pin_file(path, provider, session),
        "bound_at_epoch": time.time(),
        "payload_sha256": _hash(text.encode()),
    }
    require_bound(bridge, artifact, binding, text)
    return binding


def _legacy_binding(binding):
    """Use the existing strict record matcher for the target transcript only."""
    result = dict(binding, schema="native-delivery-v1")
    result["identity"] = {
        "workspace_uuid": binding["identity"]["target_workspace_uuid"],
        "caller_surface_uuid": binding["identity"]["caller_surface_uuid"],
        "target_surface_uuid": binding["identity"]["target_surface_uuid"],
        "target_pane_uuid": binding["identity"]["target_pane_uuid"],
    }
    return result


def require_bound(bridge, artifact, binding, text):
    if not isinstance(binding, dict) or binding.get("schema") != SCHEMA:
        raise native.NativeDeliveryError("ORIGINAL_SUCCESSOR_BINDING_REQUIRED")
    if (binding.get("artifact_sha256") != _hash(_canonical(artifact))
            or binding.get("binding_mode") != artifact.get("binding_mode")):
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_AUTHORIZATION_CHANGED")
    native._validate_binding(_legacy_binding(binding), text)
    pair = bridge.pin_successor_pair(artifact)
    identity = binding.get("identity", {})
    expected = {
        "caller_workspace_uuid": pair["caller_workspace_uuid"],
        "caller_surface_uuid": pair["caller_surface_uuid"],
        "caller_pane_uuid": pair["caller_pane_uuid"],
        "target_workspace_uuid": pair["target_workspace_uuid"],
        "target_surface_uuid": pair["target_surface_uuid"],
        "target_pane_uuid": pair["target_pane_uuid"],
    }
    if identity != expected or binding.get("payload_sha256") != _hash(text.encode()):
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_IDENTITY_CHANGED")
    row = _target_row(bridge, pair["target_surface_uuid"])
    if (row["workspace_uuid"].upper() != pair["target_workspace_uuid"].upper()
            or row["target_pane_uuid"].upper() != pair["target_pane_uuid"].upper()
            or row["tty"] != binding.get("target_tty")):
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_TARGET_MOVED")
    process = native.identity.process(binding["process"]["pid"])
    if native._public_process(process) != binding["process"]:
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_PROCESS_CHANGED")
    tty = native.subprocess.run(["/bin/ps", "-p", str(process["pid"]), "-o", "tty="],
                                capture_output=True, text=True, timeout=3, check=True).stdout.strip()
    if tty != binding["target_tty"]:
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_PROCESS_TTY_CHANGED")
    if process["env"].get("CMUX_SURFACE_ID", "").upper() != pair["target_surface_uuid"].upper() or process["env"].get("CMUX_WORKSPACE_ID", "").upper() != pair["target_workspace_uuid"].upper():
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_PROCESS_SCOPE_CHANGED")
    if native._provider(process) != binding["provider"] or native._live_session(process, binding["provider"]) != binding["session_id"]:
        raise native.NativeDeliveryError("NATIVE_SUCCESSOR_SESSION_CHANGED")
    with native._open(binding["transcript"]["path"]) as handle:
        native._check_file(handle, _legacy_binding(binding))
    return pair


def scan_received(bridge, artifact, binding, text, *, paste_fence=None, not_before=None):
    """Read complete native records after the original persisted paste fence."""
    require_bound(bridge, artifact, binding, text)
    legacy = _legacy_binding(binding)
    result = native.probe(legacy, text, paste_fence=paste_fence, not_before=not_before)
    require_bound(bridge, artifact, binding, text)
    return dict(result, observed_at=time.time(), binding=binding)


def _exclusive_json(path, value):
    """Immutable original evidence is fsynced before the next terminal action."""
    path = Path(path)
    raw = _canonical(value) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _paths(artifact, marker, state_root=None):
    root = Path(state_root) if state_root is not None else Path.home() / ".local/state/multi-agent-collaboration"
    if not root.is_absolute():
        raise ValueError("SUCCESSOR_JOURNAL_REQUIRES_ABSOLUTE_ROOT")
    caller = successor_rebind._uuid(artifact["successor_surface_uuid"], "successor_surface_uuid")
    key = _hash(_canonical([caller, marker]))
    return root, root / "successor-dispatch-v2" / key


@contextlib.contextmanager
def _locked(root, journal, artifact):
    """Serialize with ordinary senders, task dispatch and sibling target tabs."""
    legacy = root / "deliveries-v1"
    task = root / "task-dispatch-v1"
    for directory in (legacy, task, journal):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = successor_rebind._uuid(artifact["target_workspace_uuid"], "target_workspace_uuid")
    pane = successor_rebind._uuid(artifact["target_pane_uuid"], "target_pane_uuid")
    target = successor_rebind._uuid(artifact["target_executor_uuid"], "target_executor_uuid")
    locks = (legacy / ("pane-" + _hash((workspace + ":" + pane).encode()) + ".lock"),
             legacy / ("target-" + _hash(target.encode()) + ".lock"),
             task / ("target-" + _hash(target.encode()) + ".lock"), journal / "delivery.lock")
    with contextlib.ExitStack() as stack:
        for path in locks:
            lock = stack.enter_context(open_regular_lock(path))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise native.NativeDeliveryError("SUCCESSOR_DELIVERY_IN_PROGRESS: zero input") from exc
        yield


def _event(journal, phase, **fields):
    paths = list(journal.glob("event-*.json"))
    value = dict(fields, phase=phase, at_epoch=time.time())
    _exclusive_json(journal / ("event-%04d.json" % (len(paths) + 1)), value)
    return value


def _original(journal, artifact, text, marker):
    path = journal / "attempt.json"
    attempt = json.loads(read_bytes(path))
    if (attempt.get("schema") != "successor-attempt-v2"
            or attempt.get("artifact") != artifact
            or attempt.get("artifact_sha256") != _hash(_canonical(artifact))
            or attempt.get("marker") != marker or attempt.get("body") != text
            or attempt.get("body_sha256") != _hash(text.encode())):
        raise native.NativeDeliveryError("SUCCESSOR_ORIGINAL_ATTEMPT_CHANGED")
    wire = attempt["wire"]
    if attempt.get("wire_sha256") != _hash(wire.encode()):
        raise native.NativeDeliveryError("SUCCESSOR_WIRE_CHANGED")
    reference = attempt.get("body_reference")
    if reference is not None:
        if (prompt_reference.wire_reference(wire) != reference
                or prompt_reference.verify(reference, text) != attempt.get("body_pin")):
            raise native.NativeDeliveryError("SUCCESSOR_ORIGINAL_BODY_CHANGED")
    elif wire != text or attempt.get("body_pin") is not None:
        raise native.NativeDeliveryError("SUCCESSOR_ORIGINAL_BODY_CHANGED")
    events = [json.loads(read_bytes(p)) for p in sorted(journal.glob("event-*.json"))]
    pastes = [event for event in events if event.get("phase") == "PASTE_INTENT"]
    if len(pastes) > 1:
        raise native.NativeDeliveryError("SUCCESSOR_DUPLICATE_PASTE_INTENT")
    if not pastes:
        return attempt, None
    paste = pastes[0]
    if paste.get("screen_sha256") != _hash(paste.get("screen", "").encode()):
        raise native.NativeDeliveryError("SUCCESSOR_PASTE_SCREEN_CHANGED")
    native._fenced_binding(_legacy_binding(attempt["native_binding"]), wire,
                           paste.get("native_paste_fence"), paste.get("at_epoch"))
    return attempt, paste


def _observe(bridge, journal, artifact, text, marker):
    attempt, paste = _original(journal, artifact, text, marker)
    if paste is None:
        result = dict(confirmed=False, state="NO_INPUT", input_operations=0)
    else:
        result = scan_received(bridge, artifact, attempt["native_binding"], attempt["wire"],
                               paste_fence=paste["native_paste_fence"], not_before=paste["at_epoch"])
        if result.get("confirmed") is True and not native.validate_proof(
                _legacy_binding(attempt["native_binding"]), attempt["wire"], result.get("native_proof"),
                paste_fence=paste["native_paste_fence"], not_before=paste["at_epoch"]):
            raise native.NativeDeliveryError("SUCCESSOR_NATIVE_PROOF_CHANGED")
    result.update(journal=str(journal), attempt=str(journal / "attempt.json"),
                  confirmation_scope="reference_notice" if attempt.get("body_reference") else "native_user_message",
                  body_read_confirmed=False, business_accepted=False, reconciled_read_only=True,
                  input_operations=0, marker=marker)
    if attempt.get("body_reference"):
        result["body_reference"] = attempt["body_reference"]
    observation = journal / ("observation-" + str(time.time_ns()) + ".json")
    _exclusive_json(observation, result)
    result["observation"] = str(observation)
    receipt = journal / "receipt.json"
    if result.get("confirmed") is True:
        if receipt.exists():
            saved = json.loads(read_bytes(receipt))
            if (saved.get("binding") != attempt["native_binding"]
                    or saved.get("marker") != marker
                    or saved.get("confirmation_scope") != result["confirmation_scope"]
                    or not native.validate_proof(_legacy_binding(attempt["native_binding"]),
                        attempt["wire"], saved.get("native_proof"),
                        paste_fence=paste["native_paste_fence"], not_before=paste["at_epoch"])):
                raise native.NativeDeliveryError("SUCCESSOR_SAVED_RECEIPT_CHANGED")
        else:
            _exclusive_json(receipt, result)
        result["receipt"] = str(receipt)
    return result


def reconcile(bridge, artifact, text, *, marker, state_root=None):
    """Zero terminal input, using only the original durable binding and fence."""
    successor_rebind.validate_artifact(artifact)
    root, journal = _paths(artifact, marker, state_root)
    with _locked(root, journal, artifact):
        return _observe(bridge, journal, artifact, text, marker)


def submit(bridge, artifact, text, *, marker=None, key=None, state_root=None, wait_seconds=3):
    """Persist once, paste once, submit once; all later calls only reconcile.

    Old tasks, callbacks and attempts are never read or rewritten. A reference
    notice proves only receipt of that exact notice, not reading/acceptance of
    its body. The caller must supply a stable marker for long messages.
    """
    successor_rebind.validate_artifact(artifact)
    if not isinstance(text, str) or not text:
        raise native.NativeDeliveryError("NATIVE_EMPTY_PAYLOAD")
    marker = marker or "SUCCESSOR_" + _hash(text.encode())[:32]
    if marker not in text:
        raise ValueError("MESSAGE_REFERENCE_MARKER_REQUIRED")
    if key not in (None, "enter", "tab"):
        raise ValueError("SUCCESSOR_UNSUPPORTED_KEY")
    prompt_reference._ordinary(text)
    root, journal = _paths(artifact, marker, state_root)
    with _locked(root, journal, artifact):
        if (journal / "attempt.json").exists():
            return _observe(bridge, journal, artifact, text, marker)
        planned = prompt_reference.plan(text, marker, root / "message-bodies-v1")
        wire, reference = planned["text"], planned["reference"]
        body_pin = prompt_reference.persist(reference, text)
        binding = bind_target(bridge, artifact, wire)
        before = bridge.read_screen_successor(artifact)
        bridge.require_agent_input(before, binding["identity"]["target_surface_uuid"])
        if not bridge.compose_block_is_empty(before):
            raise native.NativeDeliveryError("SUCCESSOR_COMPOSE_OCCUPIED: zero input; preserve existing draft")
        if bridge.receiver_cannot_submit_now(before):
            raise native.NativeDeliveryError("SUCCESSOR_RECEIVER_NOT_READY: zero input")
        if "".join(marker.split()) in "".join(before.split()):
            raise native.NativeDeliveryError("SUCCESSOR_MARKER_ALREADY_VISIBLE: zero input")
        attempt = dict(schema="successor-attempt-v2", artifact=artifact,
                       artifact_sha256=_hash(_canonical(artifact)), marker=marker,
                       body=text, body_sha256=_hash(text.encode()), wire=wire,
                       wire_sha256=_hash(wire.encode()), body_reference=reference,
                       body_pin=body_pin, native_binding=binding, created_at_epoch=time.time())
        _exclusive_json(journal / "attempt.json", attempt)
        operations = 0
        try:
            require_bound(bridge, artifact, binding, wire)
            fence = native.capture_paste_fence(_legacy_binding(binding))
            _event(journal, "PASTE_INTENT", screen=before, screen_sha256=_hash(before.encode()),
                   native_paste_fence=fence)
            require_bound(bridge, artifact, binding, wire)
            if reference is not None and prompt_reference.verify(reference, text) != body_pin:
                raise native.NativeDeliveryError("SUCCESSOR_ORIGINAL_BODY_CHANGED")
            operations += 1
            bridge._run_successor("rpc", "terminal.paste", json.dumps({
                "text": wire, "submit_key": "none",
                "workspace_id": binding["identity"]["target_workspace_uuid"],
                "surface_id": binding["identity"]["target_surface_uuid"],
            }, ensure_ascii=False), artifact=artifact)
            _event(journal, "PASTED")
            stable = None
            for _ in range(20):
                screen = bridge.read_screen_successor(artifact)
                if bridge.receiver_cannot_submit_now(screen) or bridge.pending_queue_holds(screen, wire):
                    raise native.NativeDeliveryError("SUCCESSOR_RECEIVER_NOT_READY: preserve original draft")
                if bridge._exact_pending_text(screen, wire):
                    structure = bridge._draft_structure(screen, wire)
                    if stable is not None and stable == structure:
                        break
                    stable = structure
                else:
                    stable = None
                time.sleep(0.25)
            else:
                raise native.NativeDeliveryError("SUCCESSOR_EXACT_STABLE_DRAFT_NOT_OBSERVED")
            queue = bridge._codex_tab_queue_allowed(screen, wire)
            actual_key = "tab" if queue else "enter"
            if key is not None and key != actual_key:
                raise native.NativeDeliveryError("SUCCESSOR_KEY_NOT_SUPPORTED_BY_CURRENT_RECEIVER")
            if binding["provider"] == "codex" and bridge._queued_or_active_input(screen) and not queue:
                raise native.NativeDeliveryError("SUCCESSOR_NO_SUPPORTED_SUBMIT_ACTION")
            _event(journal, "KEY_INTENT", key=actual_key, screen=screen, screen_sha256=_hash(screen.encode()))
            require_bound(bridge, artifact, binding, wire)
            operations += 1
            bridge._run_successor("send-key", "--surface", binding["identity"]["target_surface_uuid"],
                                  "--", actual_key, artifact=artifact)
            _event(journal, "KEY_SENT", key=actual_key)
            deadline = time.monotonic() + max(0, min(float(wait_seconds), 10))
            while True:
                result = _observe(bridge, journal, artifact, text, marker)
                if result.get("confirmed") or time.monotonic() >= deadline:
                    result.update(input_operations=operations, reconciled_read_only=False, submission_key=actual_key)
                    return result
                time.sleep(0.25)
        except BaseException as exc:
            _event(journal, "ERROR", error=str(exc), input_operations_attempted=operations)
            raise
