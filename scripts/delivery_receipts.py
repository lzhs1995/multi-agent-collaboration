"""Read-only native callback acknowledgement and atomic receipts; no send budget."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util

from datetime import datetime

import hashlib

import json

import os

from pathlib import Path

import stat

import sys

import tempfile

import time

class ReceiptError(RuntimeError):
    pass

def snapshot(path):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ReceiptError("regular file required: " + str(path))
    data = path.read_bytes()
    after = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after):
        raise ReceiptError("file changed while reading: " + str(path))
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "identity": list(identity(after))}

def publish(path, value):
    """No-overwrite publication; a crash cannot expose a partially written JSON."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, path)  # atomic, fails if another sender/receiver already won
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        os.unlink(tmp)

@contextlib.contextmanager
def delivery_attempt_lock(path):
    """Share the sender's existing lock; acknowledgement never spends a retry."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReceiptError('original delivery in progress; reconcile without sending later') from exc
        yield
    finally:
        os.close(fd)

def bind(pack, bridge, *, receiver=False):
    gate = json.loads(Path(pack["identity_gate"]).read_text())
    if gate.get("task_id") != pack["task_id"] or gate.get("status") != "PASS":
        raise ReceiptError("task identity gate mismatch")
    peers = gate.get("executors") or [{"surface_ref": gate.get("executor"),
                                        "surface_uuid": gate.get("executor_surface_uuid")}]
    peers = [r for r in peers if r.get("surface_ref") == pack.get("executor")]
    if len(peers) != 1 or pack.get("callback_target") != gate.get("supervisor"):
        raise ReceiptError("callback participant mismatch")
    executor = peers[0]["surface_uuid"]
    if pack.get("executor_uuid", executor) != executor:
        raise ReceiptError("executor UUID mismatch")
    supervisor = gate["supervisor_surface_uuid"]
    return bridge.pin_workspace(
        pack["executor"] if receiver else pack["callback_target"],
        workspace_uuid=gate["workspace_uuid"],
        caller_uuid=supervisor if receiver else executor,
        target_uuid=executor if receiver else supervisor,
    )

def callback_receipt(pack, report, *, producer, evidence):
    return {"version": 2, "task_id": pack["task_id"],
            "completion_nonce": pack["completion_nonce"],
            "completion_callback": pack["completion_callback"],
            "callback_target": pack["callback_target"], "report": pack["report"],
            "report_sha256": report["sha256"], "report_bytes": report["bytes"],
            "confirmed": True, "confirmation_source": producer,
            "evidence": evidence, "recorded_at_epoch": time.time()}

def native_user_record(path, line_number, *, session_id, exact_text, after):
    """Accept only an actual user response item, never tool output/quoted prose."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or line_number < 2:
        raise ReceiptError("invalid native transcript location")
    with path.open("rb") as f:
        meta = json.loads(f.readline())
        if meta.get("type") != "session_meta" or meta.get("payload", {}).get("id") != session_id:
            raise ReceiptError("native session mismatch")
        for number, raw in enumerate(f, 2):
            if number == line_number:
                break
        else:
            raise ReceiptError("native record missing")
    event = json.loads(raw)
    payload = event.get("payload", {})
    content = payload.get("content", [])
    texts = [x.get("text") for x in content if isinstance(x, dict) and x.get("type") == "input_text"]
    if (event.get("type") != "response_item" or payload.get("type") != "message"
            or payload.get("role") != "user" or texts != [exact_text]
            or len(content) != 1):
        raise ReceiptError("record is not this exact received user message")
    if datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")) < datetime.fromisoformat(after.replace("Z", "+00:00")):
        raise ReceiptError("record predates task finalization")
    return {"path": str(path), "line": line_number, "session_id": session_id,
            "record_sha256": hashlib.sha256(raw).hexdigest(), "record": event}

def acknowledge_received_callback(pack_path, bridge, *, transcript, line,
                                  received_callback):
    """Supervisor only: close a late delivery without any paste or Enter.

    The bound bridge validates its own historical canonical skill. This supports
    pre-upgrade tasks without changing their pack or pretending a send succeeded.
    """
    pack_before = snapshot(pack_path)
    pack = bridge.validate_task_pack_contract(pack_path)
    receipt_path = Path(pack["completion_receipt"])
    journal = receipt_path.with_name(receipt_path.stem + '-attempts')
    if journal.exists() or Path(str(receipt_path) + '.pending.json').exists():
        raise ReceiptError('legacy journal requires its original reconciliation controller; no migration')
    if received_callback != pack["completion_callback"]:
        raise ReceiptError("explicit received callback differs from task")
    from availability_contract import require_action
    require_action(pack["task_id"], "callback", pack)
    gate_before = snapshot(pack["identity_gate"])
    binding = bind(pack, bridge, receiver=True)
    session_id = os.environ.get("CODEX_THREAD_ID")
    if not session_id:
        raise ReceiptError("current receiver native session is required")
    transcript = Path(transcript)
    if not transcript.resolve().is_relative_to((Path.home() / ".codex/sessions").resolve()):
        raise ReceiptError("receiver transcript must be in the native session store")
    evidence = native_user_record(transcript, line, session_id=session_id,
                                  exact_text=received_callback, after=pack["finalized_at"])
    report = snapshot(pack["report"])
    # Consume the sender's existing deliveries-v1 identity and budget. Never
    # allocate a parallel retry record or alter its Enter/Tab counters here.
    from cmux_delivery_evidence import digest
    key = digest(json.dumps([binding["target_surface_uuid"], pack["completion_nonce"]], sort_keys=True))
    durable_path = Path.home() / '.local/state/multi-agent-collaboration/deliveries-v1' / (key + '.json')
    with delivery_attempt_lock(durable_path):
        durable_pin = snapshot(durable_path) if durable_path.exists() else None
        if durable_pin is not None:
            durable = json.loads(durable_path.read_text())
            identity = durable.get('identity') or {}
            expected_identity = dict(workspace_uuid=binding['workspace_uuid'],
                                     caller_surface_uuid=binding['target_surface_uuid'],
                                     target_surface_uuid=binding['caller_surface_uuid'])
            expected_files = {str(Path(pack_path).resolve()): pack_before['sha256'],
                              str(Path(pack['report'])): report['sha256']}
            if (type(durable.get('version')) is not int or durable.get('version') != 1
                    or durable.get('marker') != pack['completion_nonce']
                    or durable.get('payload_sha256') != digest(received_callback)
                    or durable.get('binding') != expected_files
                    or any(identity.get(k) != v for k, v in expected_identity.items())
                    or not identity.get('target_pane_uuid')
                    or durable.get('paste_intent') is not True):
                raise ReceiptError('durable callback attempt or report mismatch')
        receipt_path = Path(pack["completion_receipt"])
        attempt_path = Path(str(receipt_path) + ".attempt.json")
        attempt_pin = snapshot(attempt_path) if attempt_path.exists() else None
        attempt = json.loads(attempt_path.read_text()) if attempt_path.exists() else None
        if attempt is not None:
            if (attempt.get("kind") != "callback" or attempt.get("pack") != pack_before
                    or attempt.get("gate") != gate_before or attempt.get("report") != report
                    or attempt.get("text_sha256") != hashlib.sha256(received_callback.encode()).hexdigest()):
                raise ReceiptError("original attempt or report mismatch")
        receipt = callback_receipt(pack, report, producer="supervisor_native_user_record",
                                   evidence={"native": evidence, "binding": binding,
                                             "pack": pack_before, "gate": gate_before,
                                             "legacy_without_attempt": attempt is None and durable_pin is None})
        if durable_pin is not None:
            receipt['delivery_attempt'] = str(durable_path)
            receipt['evidence']['durable_attempt'] = durable_pin
        if attempt_pin is not None:
            receipt["evidence"].update(attempt=str(attempt_path), attempt_sha256=attempt_pin["sha256"])
        if (journal.exists() or Path(str(receipt_path) + '.pending.json').exists()
                or snapshot(pack_path) != pack_before or snapshot(pack["identity_gate"]) != gate_before
                or snapshot(pack["report"]) != report
                or (attempt_pin is not None and snapshot(attempt_path) != attempt_pin)
                or (snapshot(durable_path) if durable_path.exists() else None) != durable_pin
                or native_user_record(transcript, line, session_id=session_id,
                                      exact_text=received_callback, after=pack['finalized_at']) != evidence
                or bind(pack, bridge, receiver=True) != binding):
            raise ReceiptError("late acknowledgement inputs changed")
        if receipt_path.exists():
            existing = json.loads(receipt_path.read_text())
            for field in ("task_id", "completion_nonce", "completion_callback", "report_sha256",
                          "report_bytes", "confirmed", "confirmation_source", "evidence"):
                if existing.get(field) != receipt[field]:
                    raise ReceiptError("existing receipt differs; preserve it")
            return existing
        require_action(pack["task_id"], "callback", pack)
        publish(receipt_path, receipt)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-pack', type=Path, required=True)
    parser.add_argument('--transcript', type=Path, required=True)
    parser.add_argument('--line', type=int, required=True)
    parser.add_argument('--received-callback', required=True)
    parser.add_argument('--bound-bridge', type=Path,
                        help='For an old task: its fixed release scripts/cmux_bridge.py')
    args = parser.parse_args(argv)
    if args.bound_bridge:
        pack = json.loads(args.task_pack.read_text())
        source = args.bound_bridge
        if (not source.is_absolute() or source.name != 'cmux_bridge.py'
                or source.parent.name != 'scripts'
                or source.parent.parent / 'SKILL.md' != Path(pack['required_skill'])):
            raise ReceiptError('bound bridge must belong to the original required_skill release')
        pin = snapshot(source)
        spec = importlib.util.spec_from_file_location('bound_callback_bridge', source)
        bridge = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = bridge
        spec.loader.exec_module(bridge)
        if snapshot(source) != pin:
            raise ReceiptError('bound bridge changed during import')
    else:
        import cmux_bridge as bridge
    receipt = acknowledge_received_callback(args.task_pack, bridge, transcript=args.transcript,
                                            line=args.line, received_callback=args.received_callback)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
