"""Native successor approval contracts; recognition has no application or release API."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid

MAX_FILE = 32 * 1024**2
MAX_SPAN = 1024**2
SAFE_JOB_DELTA = {"task_id", "actor", "run_dir", "outputs", "pdf_output"}
ACTOR_KEYS = ("workspace_uuid", "surface_uuid", "pane_uuid", "session_id")


class AdmissionRefused(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise AdmissionRefused(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def object_bytes(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid(_):
        raise AdmissionRefused("INVALID_JSON_CONSTANT")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=invalid)
    need(isinstance(value, dict), "JSON_OBJECT_REQUIRED")
    return value


def canonical_path(value):
    path = Path(value)
    need(path.is_absolute() and path.resolve(strict=True) == path,
         "CANONICAL_ABSOLUTE_PATH_REQUIRED")
    return path


def _identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def read_pin(ref):
    path = canonical_path(ref["path"])
    need(type(ref["bytes"]) is int and 0 <= ref["bytes"] <= MAX_FILE,
         "BOUNDED_FILE_PIN_REQUIRED")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
        before = os.fstat(handle.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE,
             "REGULAR_FILE_PIN_REQUIRED")
        raw = handle.read(MAX_FILE + 1)
        after = os.fstat(handle.fileno())
    fresh = path.stat()
    need(_identity(before) == _identity(after) == _identity(fresh),
         "FILE_PIN_IDENTITY_CHANGED")
    need(len(raw) == ref["bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"],
         "FILE_PIN_CHANGED")
    return raw


def pin(path):
    path = canonical_path(str(path))
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def read_span(ref):
    path = canonical_path(ref["path"])
    size, offset = ref["bytes"], ref["offset"]
    need(type(size) is int and 0 < size <= MAX_SPAN and type(offset) is int and offset >= 0,
         "BOUNDED_NATIVE_SPAN_REQUIRED")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
        before = os.fstat(handle.fileno())
        need(stat.S_ISREG(before.st_mode) and [before.st_dev, before.st_ino] == ref["identity"],
             "NATIVE_SOURCE_IDENTITY_CHANGED")
        need(before.st_size >= offset + size, "NATIVE_SPAN_INCOMPLETE")
        if offset:
            handle.seek(offset - 1)
            need(handle.read(1) == b"\n", "NATIVE_RECORD_BOUNDARY_REQUIRED")
        handle.seek(offset)
        raw = handle.read(size)
        handle.seek(offset)
        need(handle.read(size) == raw, "NATIVE_SPAN_CHANGED")
        after = os.fstat(handle.fileno())
    fresh = path.stat()
    need((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
         == (fresh.st_dev, fresh.st_ino), "NATIVE_SOURCE_IDENTITY_CHANGED")
    need(len(raw) == size and raw.endswith(b"\n")
         and hashlib.sha256(raw).hexdigest() == ref["sha256"], "NATIVE_SPAN_PIN_CHANGED")
    return raw


def native_record(ref):
    raw = read_span(ref)
    need(raw.count(b"\n") == 1, "ONE_COMPLETE_NATIVE_RECORD_REQUIRED")
    return object_bytes(raw)


def actor(value, *, pane=True):
    keys = ACTOR_KEYS if pane else ("workspace_uuid", "surface_uuid", "session_id")
    need(isinstance(value, dict), "ACTOR_OBJECT_REQUIRED")
    try:
        return {key: str(uuid.UUID(value[key])) for key in keys}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise AdmissionRefused("EXACT_ACTOR_UUIDS_REQUIRED") from exc


def message_text(record, role):
    payload = record.get("payload", {})
    content = payload.get("content")
    kind = "input_text" if role == "user" else "output_text"
    need(record.get("type") == "response_item" and payload.get("type") == "message"
         and payload.get("role") == role and isinstance(content, list) and bool(content),
         "NATIVE_MESSAGE_REQUIRED")
    need(all(isinstance(item, dict) and item.get("type") == kind
             and isinstance(item.get("text"), str) for item in content),
         "EXACT_NATIVE_TEXT_CONTENT_REQUIRED")
    metadata = record.get("metadata", {})
    retained = metadata.get("retained_source", {})
    retained_id = retained.get("id", {})
    need(metadata.get("client_authored") is False
         and retained_id.get("message_id") == payload.get("id")
         and retained_id.get("role") == role, "NATIVE_MESSAGE_PROVENANCE_REQUIRED")
    if role == "user":
        need(retained.get("complete") is True
             and metadata.get("mcp_attribution", {}).get("status") == "none",
             "COMPLETE_NATIVE_USER_INPUT_REQUIRED")
    return "".join(item["text"] for item in content)


def has_uuid(text, value):
    return re.search(r"(?<![0-9a-f-])" + re.escape(value) + r"(?![0-9a-f-])",
                     text, flags=re.IGNORECASE) is not None


def verify_chain(proposal, contract, old_job, new_job, original_validator):
    """An original applied approval plus an exact same-pane native inheritance."""
    need(proposal.get("schema") == "cmaverse-current-successor-native-approval-chain-proposal-v1",
         "SUPPORTED_CHAIN_PROPOSAL_REQUIRED")
    need(contract.get("schema") == "word-restricted-disposition-v2"
         and contract.get("scope") == "OWNED_REVISION_ROUNDTRIP",
         "APPLIED_ORIGINAL_REVISION_CONTRACT_REQUIRED")
    current, predecessor = actor(proposal["actor"]), actor(proposal["predecessor_actor"])
    need(predecessor == actor(contract["successor"])
         == actor(contract["administrator"]), "ORIGINAL_PREDECESSOR_SCOPE_CHANGED")
    need(actor(proposal["legacy_owner"], pane=False)
         == actor(contract["original_owner"], pane=False), "ORIGINAL_ORDINARY_OWNER_CHANGED")
    need(contract["original_owner_session_id"] == contract["original_owner"]["session_id"],
         "ORIGINAL_OWNER_SESSION_CHANGED")
    need(all(current[key] == predecessor[key] for key in ACTOR_KEYS[:-1])
         and current["session_id"] != predecessor["session_id"]
         and current["session_id"] != contract["original_owner_session_id"],
         "EXACT_SAME_PANE_SUCCESSOR_REQUIRED")
    original_validator(contract)

    refs = proposal["native_references"]
    need(set(refs) == set(proposal["native_record_copies"]) == {
        "predecessor_plan", "predecessor_approval", "current_inheritance",
        "current_plan", "current_approval"}, "EXACT_FIVE_NATIVE_RECORDS_REQUIRED")
    need(refs["predecessor_plan"] == contract["approved_plan"]
         and refs["predecessor_approval"] == contract["approval"],
         "ORIGINAL_NATIVE_APPROVAL_CHANGED")
    for label, ref in refs.items():
        copy = proposal["native_record_copies"][label]
        need(copy["sha256"] == ref["sha256"] and copy["bytes"] == ref["bytes"],
             "NATIVE_COPY_BINDING_CHANGED")
        need(read_pin(copy) == read_span(ref), "NATIVE_COPY_CHANGED")

    inheritance_ref, plan_ref, approval_ref = (
        refs["current_inheritance"], refs["current_plan"], refs["current_approval"])
    need(inheritance_ref["path"] == plan_ref["path"] == approval_ref["path"]
         and inheritance_ref["identity"] == plan_ref["identity"] == approval_ref["identity"],
         "ONE_CURRENT_NATIVE_ROLLOUT_REQUIRED")
    need(current["session_id"] in Path(plan_ref["path"]).name,
         "CURRENT_NATIVE_SESSION_REQUIRED")
    need(inheritance_ref["offset"] + inheritance_ref["bytes"] <= plan_ref["offset"]
         and plan_ref["offset"] + plan_ref["bytes"] < approval_ref["offset"]
         and approval_ref["offset"] - plan_ref["offset"] <= MAX_SPAN,
         "CURRENT_NATIVE_CAUSAL_ORDER_REQUIRED")
    inheritance, plan, approval = (native_record(ref) for ref in
                                  (inheritance_ref, plan_ref, approval_ref))
    inheritance_text = message_text(inheritance, "user")
    plan_text = message_text(plan, "assistant")
    need(message_text(approval, "user").strip() == "Implement the plan.",
         "EXPLICIT_CURRENT_NATIVE_APPROVAL_REQUIRED")
    need(has_uuid(inheritance_text, predecessor["session_id"])
         and all(has_uuid(inheritance_text, current[key]) for key in ACTOR_KEYS[:-1]),
         "EXPLICIT_CURRENT_INHERITANCE_REQUIRED")
    need(has_uuid(plan_text, predecessor["session_id"])
         and all(has_uuid(plan_text, current[key]) for key in ACTOR_KEYS),
         "CURRENT_PLAN_IDENTITY_REQUIRED")
    stripped = plan_text.strip()
    need(stripped.startswith("<proposed_plan>") and stripped.endswith("</proposed_plan>")
         and stripped.count("<proposed_plan>") == stripped.count("</proposed_plan>") == 1,
         "CLOSED_CURRENT_PROPOSED_PLAN_PAYLOAD_REQUIRED")
    need(new_job["input"]["path"] in plan_text, "CURRENT_PLAN_INPUT_REQUIRED")

    gap = proposal["between_current_plan_and_approval"]
    need(gap["path"] == plan_ref["path"]
         and gap["offset"] == plan_ref["offset"] + plan_ref["bytes"]
         and gap["bytes"] == approval_ref["offset"] - gap["offset"],
         "COMPLETE_CURRENT_APPROVAL_GAP_REQUIRED")
    span_ref = {key: gap[key] for key in ("path", "offset", "bytes", "sha256")}
    span_ref["identity"] = plan_ref["identity"]
    raw_gap = read_span(span_ref)
    need(read_pin(gap["copy"]) == raw_gap, "CURRENT_GAP_COPY_CHANGED")
    records = [object_bytes(line) for line in raw_gap.splitlines()]
    for record in records:
        payload = record.get("payload", {})
        need(not (record.get("type") == "response_item" and payload.get("type") == "message"
                  and payload.get("role") == "user")
             and not (record.get("type") == "event_msg"
                      and payload.get("type") == "user_message"),
             "INTERVENING_NATIVE_USER_MESSAGE")
    ordinals = [plan.get("ordinal")] + [record.get("ordinal") for record in records] + [approval.get("ordinal")]
    need(all(type(value) is int for value in ordinals)
         and ordinals == list(range(ordinals[0], ordinals[0] + len(ordinals))),
         "COMPLETE_ORDERED_NATIVE_APPROVAL_SPAN_REQUIRED")
    plan_order, approval_order = (record["metadata"].get("user_input_order") for record in (plan, approval))
    need(type(plan_order) is int and type(approval_order) is int and approval_order == plan_order + 1,
         "EXACT_CURRENT_NATIVE_USER_ORDER_REQUIRED")

    need(set(old_job) == set(new_job), "ORIGINAL_JOB_FIELDS_CHANGED")
    changed = {key for key in old_job if old_job[key] != new_job[key]}
    need(changed <= SAFE_JOB_DELTA, "ORIGINAL_JOB_SCOPE_OR_INPUT_CHANGED")
    need(old_job.get("schema") == new_job.get("schema") == "owned-initial-word-job-v1"
         and actor(old_job["actor"]) == predecessor and actor(new_job["actor"]) == current,
         "EXACT_CURRENT_INITIAL_JOB_ACTOR_REQUIRED")
    need(new_job.get("native_actions") == ["PRESERVE_TRACKED", "ACCEPT_ALL", "REJECT_ALL", "PDF_EXPORT"]
         and new_job.get("zotero_refresh") is False
         and new_job.get("final_paper_acceptance") is False,
         "UNCHANGED_INITIAL_NATIVE_CAPABILITIES_REQUIRED")
    return {
        "original_owner_preserved": contract["original_owner"],
        "original_contract_validated": True,
        "native_record_copies_verified": len(refs),
        "current_gap_records": len(records),
        "intervening_native_user_messages": 0,
        "closed_current_plan_payload": True,
        "current_plan_retained_source_complete": plan["metadata"]["retained_source"].get("complete"),
        "current_plan_text_sha256": hashlib.sha256(plan_text.encode("utf-8")).hexdigest(),
        "current_approval_text": "Implement the plan.",
        "changed_job_fields": sorted(changed),
        "unchanged_job_fields": sorted(set(new_job) - changed),
        "current_actor": current,
    }


@contextmanager
def original_controller(plan, broker_pin):
    """Compile the exact pinned original sources, without trusting cached pyc."""
    root = canonical_path(plan["controller_source"])
    scripts = root / "scripts"
    closure = plan["controller_pins"]
    need(set(closure) == {path.name for path in scripts.glob("*.py")},
         "COMPLETE_ORIGINAL_CONTROLLER_CLOSURE_REQUIRED")
    source_bytes = {}
    for name, ref in closure.items():
        need(ref["path"] == str(scripts / name), "ORIGINAL_CONTROLLER_MEMBER_PATH_CHANGED")
        source_bytes[name[:-3]] = read_pin(ref)
    need(closure["word_restricted_broker.py"] == broker_pin,
         "ORIGINAL_BROKER_PIN_CHANGED")
    created = set()

    class SourceLoader(importlib.abc.Loader):
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            created.add(module.__name__)
            exec(compile(source_bytes[module.__name__], module.__file__, "exec"), module.__dict__)

    class SourceFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in source_bytes:
                return importlib.util.spec_from_file_location(
                    fullname, str(scripts / (fullname + ".py")), loader=SourceLoader())
            return None

    need(not (set(source_bytes) & set(sys.modules)), "ORIGINAL_CONTROLLER_MODULE_CACHE_PRESENT")
    finder = SourceFinder()
    sys.meta_path.insert(0, finder)
    try:
        yield __import__("word_restricted_broker")
    finally:
        sys.meta_path.remove(finder)
        for name in created:
            sys.modules.pop(name, None)


def legacy_snapshot(root, proposal, contract):
    db_path = canonical_path(str(Path(root) / "queue.sqlite3"))
    connection = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        applied = connection.execute(
            "SELECT * FROM word_restricted_revision_dispositions WHERE id=?",
            (proposal["original_applied_disposition"]["id"],)).fetchone()
        need(applied is not None and dict(applied) == proposal["original_applied_disposition"],
             "APPLIED_ORIGINAL_DISPOSITION_CHANGED")
        need(applied["original_ticket"] == contract["original_ticket"]
             and applied["job_sha256"] == contract["initial_job"]["sha256"]
             and object_bytes(applied["contract"].encode("utf-8")) == proposal["original_contract"],
             "APPLIED_ORIGINAL_CONTRACT_BINDING_CHANGED")
        row = connection.execute("SELECT * FROM tickets WHERE id=?",
                                 (contract["original_ticket"],)).fetchone()
        need(row is not None and digest(dict(row)) == contract["original_row_sha256"],
             "ORIGINAL_ORDINARY_TICKET_PREIMAGE_CHANGED")
        owner = contract["original_owner"]
        need(row["workspace_uuid"] == owner["workspace_uuid"]
             and row["surface_uuid"] == owner["surface_uuid"],
             "ORIGINAL_ORDINARY_TICKET_OWNER_CHANGED")
        resource = connection.execute("SELECT * FROM resources WHERE name='word-zotero'").fetchone()
        need(resource is not None and digest(dict(resource)) == contract["original_resource_sha256"],
             "ORIGINAL_RESOURCE_MAPPING_CHANGED")
        tickets = list(connection.execute(
            "SELECT * FROM word_restricted_tickets WHERE disposition=?", (applied["id"],)))
        return {
            "database_path": str(db_path),
            "query_only_connection": True,
            "applied_original_disposition": dict(applied),
            "ordinary_ticket": {"id": row["id"], "status": row["status"],
                                "invocations": row["invocations"], "row_sha256": digest(dict(row))},
            "resource_sha256": digest(dict(resource)),
            "restricted_tickets": [
                {"id": item["id"], "status": item["status"], "invocations": item["invocations"],
                 "row_sha256": digest(dict(item))} for item in tickets],
        }
    finally:
        connection.close()


def review(proposal_ref, *, root):
    proposal = object_bytes(read_pin(proposal_ref))
    contract = object_bytes(read_pin(proposal["original_contract"]))
    old_job = object_bytes(read_pin(contract["initial_job"]))
    new_job = object_bytes(read_pin(proposal["current_job"]))
    current_plan = object_bytes(read_pin(proposal["current_plan"]))
    need(current_plan["initial_job"] == proposal["current_job"],
         "EXACT_CURRENT_EXECUTION_PLAN_JOB_REQUIRED")
    with original_controller(current_plan, proposal["controller_broker_source"]) as original:
        chain = verify_chain(proposal, contract, old_job, new_job, original.approved_plan)
        original.verify_closure(current_plan)
        snapshot = legacy_snapshot(root, proposal, contract)
    return {
        "schema": "word-native-successor-admission-readonly-v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "outcome": "CURRENT_NATIVE_APPROVAL_CHAIN_RECOGNIZED",
        "scope": "APPROVAL_RECOGNITION_ONLY",
        "proposal": proposal_ref,
        "original_contract": proposal["original_contract"],
        "current_job": proposal["current_job"],
        "current_execution_plan": proposal["current_plan"],
        "source": pin(Path(__file__).resolve()),
        "chain": chain,
        "current_initial_plan_complete_original_closure_pass": True,
        "legacy_readonly_snapshot": snapshot,
        "execution_admission_issued": False,
        "new_dispositions_applied": 0,
        "new_tickets": 0,
        "application_calls": 0,
        "queue_writes": 0,
        "legacy_released": False,
        "scientific_acceptance": False,
        "remaining_execution_gates": [
            "Authenticate actual normal loading in the original current actor.",
            "Close out original pending Word ticket using its separately approved original slots.",
            "Pass unchanged original legacy, serial queue, Word and focus gates before any new job.",
        ],
    }


NATIVE_SCHEMA = "word-restricted-native-successor-disposition-v1"


def make_native_disposition(proposal_ref):
    """Create a distinct prospective contract; never rewrite the applied parent."""
    proposal = object_bytes(read_pin(proposal_ref))
    original = object_bytes(read_pin(proposal["original_contract"]))
    result = dict(original)
    result.update(
        schema=NATIVE_SCHEMA,
        administrator=proposal["actor"],
        successor=proposal["actor"],
        approval=proposal["native_references"]["current_approval"],
        approved_plan=proposal["native_references"]["current_plan"],
        approval_copy=proposal["native_record_copies"]["current_approval"],
        approved_plan_copy=proposal["native_record_copies"]["current_plan"],
        initial_job=proposal["current_job"],
        native_approval_chain=proposal_ref,
        native_approval_source=pin(Path(__file__).resolve()),
    )
    return result


def _verify_native_disposition_child(contract, *, root):
    need(contract.get("schema") == NATIVE_SCHEMA
         and contract.get("scope") == "OWNED_REVISION_ROUNDTRIP",
         "EXACT_NATIVE_SUCCESSOR_DISPOSITION_REQUIRED")
    proposal_ref = contract["native_approval_chain"]
    proposal = object_bytes(read_pin(proposal_ref))
    need(contract == make_native_disposition(proposal_ref),
         "NATIVE_DISPOSITION_SCOPE_OR_SOURCE_CHANGED")
    read_pin(contract["native_approval_source"])
    with _native_parent_connection(root) as connection:
        verify_applied_native_parent(connection, contract)
    original = object_bytes(read_pin(proposal["original_contract"]))
    old_job = object_bytes(read_pin(original["initial_job"]))
    new_job = object_bytes(read_pin(proposal["current_job"]))
    candidate = object_bytes(read_pin(proposal["current_plan"]))
    need(candidate["initial_job"] == proposal["current_job"],
         "EXACT_CURRENT_EXECUTION_PLAN_JOB_REQUIRED")
    with original_controller(candidate, proposal["controller_broker_source"]) as predecessor:
        chain = verify_chain(proposal, original, old_job, new_job,
                             predecessor.approved_plan)
        predecessor.verify_closure(candidate, running=True)
    return {"outcome": "NATIVE_SUCCESSOR_DISPOSITION_VERIFIED", "chain": chain,
            "original_controller_closure_verified": True,
            "application_calls": 0, "queue_writes": 0}


@contextmanager
def _native_parent_connection(root):
    root = canonical_path(str(root))
    connection = sqlite3.connect((root / "queue.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def verify_native_disposition(contract, *, root=None):
    """Isolate the pinned .77 imports from the current broker's module cache."""
    import subprocess

    source = contract["native_approval_source"]
    need(source["path"] == str(Path(__file__).resolve()),
         "EXACT_NATIVE_APPROVAL_SOURCE_REQUIRED")
    before = read_pin(source)
    root = canonical_path(str(root or Path.home() / ".local/state/multi-agent-collaboration/resources"))
    with _native_parent_connection(root) as connection:
        verify_applied_native_parent(connection, contract)
    raw = canonical(contract)
    need(len(raw) <= MAX_SPAN, "BOUNDED_NATIVE_DISPOSITION_REQUIRED")
    try:
        result = subprocess.run(
            [str(Path(sys.executable).resolve()), "-BE", source["path"],
             "--verify-disposition-stdin", "--root", str(root)],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdmissionRefused("NATIVE_VERIFICATION_TIMEOUT_NO_EXECUTION") from exc
    need(read_pin(source) == before, "NATIVE_APPROVAL_SOURCE_CHANGED")
    need(len(result.stdout) <= MAX_SPAN and len(result.stderr) <= MAX_SPAN,
         "BOUNDED_NATIVE_VERIFICATION_RESULT_REQUIRED")
    value = object_bytes(result.stdout)
    need(result.returncode == 0
         and value.get("outcome") == "NATIVE_SUCCESSOR_DISPOSITION_VERIFIED"
         and value.get("application_calls") == value.get("queue_writes") == 0,
         "NATIVE_SUCCESSOR_DISPOSITION_REFUSED:" + str(value.get("reason", "")))
    return value


def verify_applied_native_parent(connection, contract):
    """Read the actual same-database parent; no receipt can replace its row."""
    proposal = object_bytes(read_pin(contract["native_approval_chain"]))
    expected = proposal["original_applied_disposition"]
    row = connection.execute(
        "SELECT * FROM word_restricted_revision_dispositions WHERE id=?",
        (expected["id"],),
    ).fetchone()
    need(row is not None and dict(row) == expected,
         "APPLIED_ORIGINAL_DISPOSITION_CHANGED")
    original = object_bytes(read_pin(proposal["original_contract"]))
    need(row["original_ticket"] == original["original_ticket"]
         and row["job_sha256"] == original["initial_job"]["sha256"]
         and object_bytes(row["contract"].encode("utf-8"))
         == proposal["original_contract"],
         "APPLIED_ORIGINAL_CONTRACT_BINDING_CHANGED")
    tickets = list(connection.execute(
        "SELECT * FROM word_restricted_tickets WHERE disposition=?", (expected["id"],)))
    need(len(tickets) == 1 and tickets[0]["invocations"] == 1,
         "EXACT_CONSUMED_ORIGINAL_CONTROLLER_REQUIRED")
    executed = object_bytes(read_pin(object_bytes(tickets[0]["plan"].encode("utf-8"))))
    candidate = object_bytes(read_pin(proposal["current_plan"]))
    need(executed["operation"] == "OWNED_REVISION_ROUNDTRIP"
         and executed["initial_job"] == original["initial_job"],
         "ORIGINAL_EXECUTED_PLAN_BINDING_CHANGED")
    for key in ("controller_source", "controller_pins", "service_pins", "validator_pins"):
        need(candidate[key] == executed[key], "APPLIED_ORIGINAL_CONTROLLER_CHANGED:" + key)
    need(candidate["controller_pins"].get("word_restricted_broker.py")
         == proposal["controller_broker_source"], "APPLIED_ORIGINAL_BROKER_CHANGED")


def verify_native_execution_plan(contract, execution):
    proposal = object_bytes(read_pin(contract["native_approval_chain"]))
    candidate = object_bytes(read_pin(proposal["current_plan"]))
    need(set(execution) == set(candidate), "NATIVE_EXECUTION_PLAN_FIELDS_CHANGED")
    changed = {key for key in candidate if candidate[key] != execution[key]}
    need(changed <= {"controller_source", "controller_pins"},
         "NATIVE_EXECUTION_SCOPE_OR_INPUT_CHANGED")
    need(execution["controller_pins"].get("word_native_approval.py")
         == contract["native_approval_source"],
         "COMPLETE_NATIVE_EXECUTION_SOURCE_REQUIRED")
    need(execution["initial_job"] == proposal["current_job"],
         "EXACT_CURRENT_EXECUTION_PLAN_JOB_REQUIRED")



def freeze(path, value):
    path = Path(path)
    need(path.is_absolute() and path.parent.resolve(strict=True) == path.parent,
         "CANONICAL_NEW_OUTPUT_REQUIRED")
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444), "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return pin(path)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 3 and arguments[:2] == ["--verify-disposition-stdin", "--root"]:
        try:
            raw = sys.stdin.buffer.read(MAX_SPAN + 1)
            need(len(raw) <= MAX_SPAN, "BOUNDED_NATIVE_DISPOSITION_REQUIRED")
            result = _verify_native_disposition_child(object_bytes(raw), root=arguments[2])
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except (AdmissionRefused, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            print(json.dumps({"outcome": "NATIVE_SUCCESSOR_DISPOSITION_REFUSED",
                              "reason": str(exc), "application_calls": 0,
                              "queue_writes": 0}, ensure_ascii=False))
            return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--proposal-sha256", required=True)
    parser.add_argument("--root", default=str(Path.home() / ".local/state/multi-agent-collaboration/resources"))
    parser.add_argument("--output")
    parser.add_argument("--build-native-disposition", action="store_true")
    args = parser.parse_args(argv)
    try:
        proposal_ref = pin(args.proposal)
        need(proposal_ref["sha256"] == args.proposal_sha256, "EXACT_CHAIN_PROPOSAL_SHA_REQUIRED")
        if args.build_native_disposition:
            need(args.output is not None, "CREATE_ONLY_NATIVE_DISPOSITION_OUTPUT_REQUIRED")
            result = make_native_disposition(proposal_ref)
            verify_native_disposition(result, root=args.root)
        else:
            result = review(proposal_ref, root=args.root)
        if args.output:
            result_ref = freeze(args.output, result)
            print(json.dumps({"outcome": result.get("outcome", "PROSPECTIVE_NATIVE_DISPOSITION_CREATED"),
                              "result": result_ref, "queue_writes": 0,
                              "application_calls": 0}, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AdmissionRefused, OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"outcome": "CURRENT_NATIVE_ADMISSION_REFUSED", "reason": str(exc),
                          "application_calls": 0, "queue_writes": 0, "new_tickets": 0,
                          "legacy_released": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
