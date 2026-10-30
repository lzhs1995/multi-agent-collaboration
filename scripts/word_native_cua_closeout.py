"""Close one proven .77 owned-stage failure without another application call.

Only the approved current native successor applies. The original result, slots,
stage, ordinary owner and FIFO remain untouched. The exact owned pending bytes
are archived before their canonical pointer is retired after the ticket CAS.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time

import cmux_daemon_identity as identity
import cmux_identity_budget as budget
import word_restricted_broker as protocol
import word_native_approval as approval
from resource_broker import group_alive

SCHEMA = "word-owned-native-cua-closeout-v1"
TERMINAL = "FAILED_NATIVE_CLOSED"
ROW_FIELDS = {"id", "disposition", "plan", "status", "requested", "token", "expires",
              "nonce", "parent_pid", "pgid", "invocations", "result"}
CLOSE_CODE = "await closeWord.click(closeOwnedWindowIndex); await closeWord.getAXState();"


def text(output):
    if isinstance(output, list):
        return "\n".join(part.get("text", "") for part in output if isinstance(part, dict))
    return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)


def observe_file(path):
    path = Path(path)
    before = path.lstat()
    protocol.need(path.resolve(strict=True) == path and not path.is_symlink()
                  and stat.S_ISREG(before.st_mode), "CANONICAL_REGULAR_CLOSEOUT_FILE_REQUIRED")
    raw = protocol.read_pin(protocol.pin(path))
    after = path.lstat()
    fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    protocol.need(fields(before) == fields(after), "CLOSEOUT_FILE_CHANGED_DURING_READ")
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                device=after.st_dev, inode=after.st_ino), raw


@contextmanager
def locked(path, expected):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r+b") as stream:
        info = os.fstat(stream.fileno())
        protocol.need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                      and [info.st_dev, info.st_ino] == expected, "ORIGINAL_CLOSEOUT_LOCK_CHANGED")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fresh = Path(path).lstat()
        protocol.need(stat.S_ISREG(fresh.st_mode) and fresh.st_nlink == 1
                      and [fresh.st_dev, fresh.st_ino] == expected,
                      "ORIGINAL_CLOSEOUT_LOCK_REPLACED")
        yield


def native_result(ref, actor, *, source_path):
    result = protocol.object_bytes(protocol.read_pin(ref))
    protocol.need(result.get("schema") == "cmaverse-current-native-cua-saved-close-result-v1"
                  and result.get("actor_session") == actor["session_id"],
                  "EXACT_APPROVED_NATIVE_CLOSE_RESULT_REQUIRED")
    manifest = protocol.object_bytes(protocol.read_pin(result["native_proof"]))
    source = Path(manifest["source"])
    before = source.lstat()
    protocol.need(str(source) == source_path and source.resolve(strict=True) == source
                  and actor["session_id"] in source.name
                  and before.st_ino == manifest["inode"] and stat.S_ISREG(before.st_mode),
                  "ORIGINAL_NATIVE_CLOSE_SOURCE_CHANGED")
    calls, outputs = {}, {}
    with source.open("rb") as stream:
        meta = protocol.object_bytes(stream.readline(1024**2))
        payload = meta.get("payload", {})
        aliases = [payload[key] for key in ("session_id", "id") if key in payload]
        protocol.need(meta.get("type") == "session_meta" and aliases
                      and all(item == actor["session_id"] for item in aliases),
                      "EXACT_ORIGINAL_CLOSE_SESSION_REQUIRED")
        for item in manifest["records"]:
            raw = protocol.read_pin(item, limit=1024**2)
            protocol.need(type(item.get("offset")) is int and item["offset"] >= 0
                          and raw.endswith(b"\n") and raw.count(b"\n") == 1,
                          "ONE_COMPLETE_ORIGINAL_NATIVE_CLOSE_RECORD_REQUIRED")
            if item["offset"]:
                stream.seek(item["offset"] - 1)
                protocol.need(stream.read(1) == b"\n", "ORIGINAL_NATIVE_CLOSE_BOUNDARY_CHANGED")
            stream.seek(item["offset"])
            protocol.need(stream.read(item["bytes"]) == raw, "ORIGINAL_NATIVE_CLOSE_SPAN_CHANGED")
            record = protocol.object_bytes(raw)
            value = record["payload"]
            kind = value.get("type")
            protocol.need(record.get("type") == "response_item"
                          and record["timestamp"] == item["timestamp"] and kind == item["type"]
                          and value.get("call_id") == item["call_id"],
                          "ORIGINAL_NATIVE_CLOSE_ALIAS_CHANGED")
            protocol.need(kind in ("function_call", "custom_tool_call", "function_call_output",
                                   "custom_tool_call_output"), "SUPPORTED_NATIVE_CLOSE_RECORD_REQUIRED")
            target = calls if kind in ("function_call", "custom_tool_call") else outputs
            protocol.need(value["call_id"] not in target, "DUPLICATE_NATIVE_CLOSE_CALL_OR_OUTPUT")
            target[value["call_id"]] = (item, value)
    protocol.need(len(calls) == len(outputs) == 24 and calls.keys() == outputs.keys()
                  and source.lstat().st_ino == before.st_ino, "COMPLETE_24_NATIVE_CLOSE_PAIRS_REQUIRED")
    protocol.need(all(calls[key][0]["offset"] < outputs[key][0]["offset"] for key in calls),
                  "ORIGINAL_NATIVE_CLOSE_CAUSAL_ORDER_CHANGED")
    close = []
    pres, posts = [], []
    for key, (item, value) in calls.items():
        if value["type"] != "function_call":
            continue
        arguments = protocol.object_bytes(value["arguments"].encode())
        if arguments.get("code", "").strip() == CLOSE_CODE:
            close.append((item, value, text(outputs[key][1]["output"])))
        output = text(outputs[key][1]["output"])
        if value.get("namespace") == "mcp__cua_repl" and value.get("name") == "js":
            if "documentMenuCount: 1" in output and "exactSavedOwnedDocument: true" in output:
                pres.append(item)
            if "openDocumentMenuEntries: 0, entries: []" in output:
                posts.append(item)
    protocol.need(len(close) == len(pres) == len(posts) == 1
                  and close[0][1].get("namespace") == "mcp__cua_repl"
                  and close[0][1].get("name") == "js" and "-10005" in close[0][2]
                  and pres[0]["offset"] < close[0][0]["offset"] < posts[0]["offset"],
                  "EXACT_SAVED_CLOSE_AND_INDEPENDENT_POST_REQUIRED")
    for observed, declared in ((pres[0], result["native_pre"]),
                               (posts[0], result["native_post"])):
        actual = outputs[observed["call_id"]][0]
        protocol.need(all(actual[key] == declared[key] for key in
                          ("path", "source", "offset", "bytes", "sha256", "timestamp", "call_id")),
                      "EXACT_DECLARED_NATIVE_CLOSE_OBSERVATION_REQUIRED")
    held = protocol.object_bytes(protocol.read_pin(result["held_guard"]))
    post = protocol.object_bytes(protocol.read_pin(result["independent_post"]))
    action = protocol.object_bytes(protocol.read_pin(result["action_slot"]))
    protocol.read_pin(result["phase_slot"])
    protocol.need(action.get("actor_session") == actor["session_id"]
                  and action.get("close_budget") == 1 and action.get("no_retry") is True
                  and all(action["held_record"][key] == result["held_guard"][key]
                          for key in ("path", "bytes", "sha256"))
                  and result.get("original_action_budget_remaining") == 0
                  and result.get("original_phase_budget_remaining") == 0,
                  "EXACT_CONSUMED_NATIVE_CLOSE_SLOT_REQUIRED")
    protocol.need(held["baseline"] == post["state"] and held["fds"].get("owned_open") is True
                  and held["fds"]["returncode"] == 0 and post["owned_fds"]["owned_open"] is False
                  and post["owned_fds"]["returncode"] == 1 and not post["owned_fds"]["stdout"]
                  and not post["owned_fds"]["stderr"], "ORIGINAL_LOCKED_CLOSE_FD_POST_REQUIRED")
    protocol.need(result.get("old_helper_exit", {}).get("code") == 2
                  and result.get("queue_mutations") == result.get("release_operations") == 0,
                  "ORIGINAL_NATIVE_FAILURE_AND_OWNERS_PRESERVED_REQUIRED")
    return result, held, dict(native_pairs=24, native_source_inode=before.st_ino,
                              native_source_device_observed=before.st_dev,
                              original_close_error="-10005 timeoutReached", original_helper_exit=2)


def archived_file(path, pending_ref, raw):
    if path.exists():
        ref, actual = observe_file(path)
        protocol.need(actual == raw and not path.lstat().st_mode & 0o222
                      and path.lstat().st_nlink == 1, "EXACT_OWNED_PENDING_ARCHIVE_REQUIRED")
        return ref
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    ref, actual = observe_file(path)
    protocol.need(actual == raw and ref["sha256"] == pending_ref["sha256"],
                  "OWNED_PENDING_ARCHIVE_CHANGED")
    return ref


class NativeCuaCloseout:
    def __init__(self, root=None, *, readonly=False):
        self.root = Path(root or Path.home() / ".local/state/multi-agent-collaboration/resources").resolve(strict=True)
        uri = (self.root / "queue.sqlite3").as_uri() + ("?mode=ro" if readonly else "?mode=rw")
        self.db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=3)
        self.db.row_factory = sqlite3.Row
        if readonly:
            self.db.execute("PRAGMA query_only=ON")

    def close(self):
        self.db.close()

    def contract(self, ref):
        value = protocol.object_bytes(protocol.read_pin(ref))
        protocol.need(set(value) == {"schema", "ticket", "native_disposition", "close_result",
                                    "pending_archive", "controller_source", "controller_pins"}
                      and value["schema"] == SCHEMA, "EXACT_NATIVE_CLOSEOUT_CONTRACT_REQUIRED")
        source = Path(value["controller_source"])
        protocol.need(source.resolve(strict=True) == source
                      and source / "scripts/word_native_cua_closeout.py" == Path(__file__).resolve(),
                      "EXACT_CLOSEOUT_CONTROLLER_REQUIRED")
        members = {item.name for item in (source / "scripts").glob("*.py")}
        protocol.need(members == set(value["controller_pins"]), "COMPLETE_CLOSEOUT_SOURCE_REQUIRED")
        for name, member in value["controller_pins"].items():
            protocol.need(member["path"] == str(source / "scripts" / name),
                          "CLOSEOUT_CONTROLLER_MEMBER_CHANGED")
            protocol.read_pin(member)
        disposition = protocol.object_bytes(protocol.read_pin(value["native_disposition"]))
        protocol.need(disposition.get("schema") == protocol.NATIVE_REVISION_SCHEMA,
                      "CURRENT_NATIVE_SUCCESSOR_CLOSEOUT_AUTHORITY_REQUIRED")
        protocol.approved_plan(disposition, root=self.root)
        return value, disposition

    def row(self, ticket):
        value = self.db.execute("SELECT * FROM word_restricted_tickets WHERE id=?", (ticket,)).fetchone()
        protocol.need(value is not None and set(value.keys()) == ROW_FIELDS, "EXACT_ORIGINAL_RESTRICTED_ROW_REQUIRED")
        return dict(value)

    def receipt(self, ticket):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='word_restricted_native_cua_closeouts'").fetchone():
            return None
        row = self.db.execute("SELECT receipt FROM word_restricted_native_cua_closeouts WHERE ticket=?", (ticket,)).fetchone()
        return protocol.object_bytes(row[0].encode()) if row else None

    def facts(self, ref, contract, disposition):
        result, held, native = native_result(contract["close_result"], disposition["successor"],
                                            source_path=disposition["approved_plan"]["path"])
        return self._facts_checked(ref, contract, disposition, result, held, native, self.row(contract["ticket"]))

    def _facts_checked(self, ref, contract, disposition, result, held, native, row):
        baseline = held["baseline"]
        action = protocol.object_bytes(protocol.read_pin(result["action_slot"]))
        protocol.need(row == baseline["restricted"] and row["status"] == "PENDING" and row["invocations"] == 1
                      and action["original_ticket"] == row["id"] and not group_alive(row["pgid"]),
                      "EXACT_DRAINED_ORIGINAL_PENDING_INVOCATION_REQUIRED")
        applied = self.db.execute("SELECT * FROM word_restricted_revision_dispositions WHERE id=?", (row["disposition"],)).fetchone()
        proposal = protocol.object_bytes(protocol.read_pin(disposition["native_approval_chain"]))
        protocol.need(applied is not None and dict(applied) == proposal["original_applied_disposition"],
                      "EXACT_APPLIED_ORIGINAL_PARENT_REQUIRED")
        original_result = protocol.object_bytes(row["result"].encode())
        error = protocol.object_bytes(original_result["stderr"].encode())
        protocol.need(error == dict(status="RESTRICTED_NATIVE_PENDING", reason="OWNED_REVISION_NATIVE_STAGE_UNCONFIRMED",
                                    error_type="ResourceBusy", no_retry=True, no_foreign_save_or_close=True,
                                    legacy_released=False) and original_result["exit_code"] == 2
                      and original_result["process_group_empty"] is True, "ORIGINAL_77_STAGE_FAILURE_REQUIRED")
        pending_path = Path.home() / ".cache/wordrev/word-automation.pending.json"
        pending_ref, raw = observe_file(pending_path)
        protocol.need(pending_ref == baseline["pending"], "ORIGINAL_OWNED_PENDING_POINTER_CHANGED")
        pending = protocol.object_bytes(raw)
        protocol.need(pending["owner"] == "restricted-revision:" + row["id"] and pending["pid"] == row["pgid"]
                      and pending["owned_document"] == baseline["owned"]["path"]
                      and pending["status"] == "UNRESOLVED_NATIVE_OPERATION" and pending["retry_permitted"] is False,
                      "EXACT_ORIGINAL_OWNED_PENDING_REQUIRED")
        old_plan = protocol.object_bytes(protocol.read_pin(protocol.object_bytes(row["plan"].encode())))
        owned_ref, _ = observe_file(baseline["owned"]["path"])
        protocol.need(owned_ref == baseline["owned"] and owned_ref["sha256"] == old_plan["input"]["sha256"]
                      and pending["evidence_dir"] == str(Path(old_plan["evidence_root"])),
                      "EXACT_ORIGINAL_NATIVE_STAGE_CHANGED")
        archive = Path(contract["pending_archive"])
        expected_archive = Path(contract["close_result"]["path"]).parent / "native_cua_saved_close_v1/ARCHIVED_ORIGINAL_WORD_PENDING.json"
        protocol.need(archive == expected_archive and archive.parent.resolve(strict=True) == archive.parent
                      and not archive.is_symlink(), "OWNED_ORIGINAL_PENDING_ARCHIVE_PATH_REQUIRED")
        if archive.exists():
            _, archived = observe_file(archive)
            protocol.need(archived == raw and not archive.lstat().st_mode & 0o222, "PENDING_ARCHIVE_CONFLICT")
        ordinary = [dict(item) for item in self.db.execute("SELECT * FROM tickets WHERE resource='word-zotero' ORDER BY id")]
        resource = dict(self.db.execute("SELECT * FROM resources WHERE name='word-zotero'").fetchone())
        protocol.need(ordinary == baseline["ordinary"] and resource == baseline["resource"],
                      "ORIGINAL_ORDINARY_OWNER_OR_FIFO_CHANGED")
        with budget.limit(12):
            word = identity.process(result["word_pid"])
            protocol.need(word["executable"] == "/Applications/Microsoft Word.app/Contents/MacOS/Microsoft Word",
                          "ORIGINAL_WORD_PROCESS_CHANGED")
            birth_text = protocol.object_bytes(protocol.read_pin(result["independent_post"]))["word_birth_observation"]["stdout"].split()
            original_birth = int(time.mktime(time.strptime(" ".join(birth_text[1:6]), "%a %b %d %H:%M:%S %Y")))
            protocol.need(word["birth"][0] == original_birth, "ORIGINAL_WORD_PROCESS_BIRTH_CHANGED")
            fds = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(word["pid"]), "-Fpcfin", owned_ref["path"]],
                                 capture_output=True, text=True, timeout=budget.timeout(5.0), check=False)
            protocol.need(fds.returncode == 1 and not fds.stdout and not fds.stderr,
                          "ORIGINAL_OWNED_DOCUMENT_STILL_OPEN_OR_UNKNOWN")
        return dict(row=row, pending=pending_ref, pending_raw_sha256=hashlib.sha256(raw).hexdigest(),
                    ordinary_sha256=protocol.digest(ordinary), resource_sha256=protocol.digest(resource),
                    owned=owned_ref, locks=held["locks"], actor=disposition["successor"],
                    word_binding={key: word[key] for key in ("pid", "birth", "executable", "file_identity")},
                    native=native, archive=str(archive))

    def preview(self, ref):
        contract, disposition = self.contract(ref)
        receipt = self.receipt(contract["ticket"])
        if receipt is not None:
            protocol.need(receipt["contract"] == ref
                          and self.row(contract["ticket"]) == dict(receipt["original_row"], status=TERMINAL),
                          "ORIGINAL_NATIVE_CLOSEOUT_RECEIPT_CHANGED")
            return dict(outcome="OWNED_NATIVE_CLOSEOUT_RECORDED", plan_sha256=receipt["plan_sha256"],
                        receipt=receipt, input_operations=0, queue_writes=0, ordinary_released=False)
        facts = self.facts(ref, contract, disposition)
        return dict(outcome="OWNED_NATIVE_CUA_CLOSEOUT_READ_ONLY_PLAN", contract=ref, facts=facts,
                    plan_sha256=protocol.digest(dict(contract=ref, facts=facts)),
                    input_operations=0, queue_writes=0, ordinary_released=False, native_retry_budget=0)

    def finish(self, receipt):
        pending = Path(receipt["pending"]["path"])
        archived = protocol.read_pin(receipt["archive"])
        archive_info = Path(receipt["archive"]["path"]).lstat()
        protocol.need(archive_info.st_nlink == 1 and not archive_info.st_mode & 0o222,
                      "IMMUTABLE_ORIGINAL_PENDING_ARCHIVE_REQUIRED")
        if pending.exists():
            ref, raw = observe_file(pending)
            protocol.need(ref == receipt["pending"] and raw == archived, "ORIGINAL_PENDING_CHANGED_BEFORE_RETIREMENT")
            os.unlink(pending)
            fd = os.open(pending.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return dict(outcome="OWNED_NATIVE_CUA_FAILURE_CLOSED", status=TERMINAL,
                    ticket=receipt["ticket"], receipt=receipt, pending_archived=True,
                    input_operations=0, native_invocations_added=0, ordinary_released=False,
                    legacy_released=False)

    def apply(self, ref, plan_sha256):
        protocol.need(sys.flags.ignore_environment and sys.dont_write_bytecode, "PYTHON_BE_REQUIRED")
        contract, disposition = self.contract(ref)
        protocol.need(protocol.actor_matches(disposition["successor"], protocol.native_identity()),
                      "ONLY_APPROVED_CURRENT_NATIVE_ACTOR_MAY_CLOSE")
        preview = self.preview(ref)
        protocol.need(preview["plan_sha256"] == plan_sha256, "EXACT_NATIVE_CLOSEOUT_PLAN_SHA_REQUIRED")
        saved = preview.get("receipt")
        locks = saved["locks"] if saved else preview["facts"]["locks"]
        with ExitStack() as stack:
            for path, expected in sorted(locks.items()):
                stack.enter_context(locked(path, expected))
            protocol.need(protocol.actor_matches(disposition["successor"], protocol.native_identity()),
                          "CURRENT_NATIVE_CLOSEOUT_ACTOR_CHANGED")
            if saved:
                return self.finish(saved)
            fresh_contract, fresh_disposition = self.contract(ref)
            protocol.need(fresh_contract == contract and fresh_disposition == disposition,
                          "ORIGINAL_NATIVE_CLOSEOUT_AUTHORITY_CHANGED")
            current = self.preview(ref)
            protocol.need(current["plan_sha256"] == plan_sha256, "NATIVE_CLOSEOUT_PREIMAGE_CHANGED")
            facts = current["facts"]
            pending_ref, pending_raw = observe_file(facts["pending"]["path"])
            archive = archived_file(Path(facts["archive"]), pending_ref, pending_raw)
            receipt = dict(schema=SCHEMA, ticket=contract["ticket"], contract=ref,
                           plan_sha256=plan_sha256, actor=disposition["successor"],
                           original_row=facts["row"], original_result_sha256=hashlib.sha256(facts["row"]["result"].encode()).hexdigest(),
                           pending=pending_ref, archive=archive, locks=locks,
                           owned_stage=facts["owned"], native=facts["native"], word_binding=facts["word_binding"],
                           status=TERMINAL, original_invocations_preserved=1,
                           original_action_remaining=0, original_phase_remaining=0,
                           ordinary_released=False, source=protocol.pin(Path(__file__).resolve()),
                           closed_at=datetime.now(timezone.utc).isoformat())
            self.db.execute("BEGIN IMMEDIATE")
            try:
                ordinary = [dict(item) for item in self.db.execute("SELECT * FROM tickets WHERE resource='word-zotero' ORDER BY id")]
                resource = dict(self.db.execute("SELECT * FROM resources WHERE name='word-zotero'").fetchone())
                transaction_pending, _ = observe_file(facts["pending"]["path"])
                approval.verify_applied_native_parent(self.db, disposition)
                transaction_owned, _ = observe_file(facts["owned"]["path"])
                result = protocol.object_bytes(protocol.read_pin(contract["close_result"]))
                protocol.read_pin(result["action_slot"])
                protocol.read_pin(result["phase_slot"])
                protocol.need(self.row(contract["ticket"]) == facts["row"]
                              and protocol.digest(ordinary) == facts["ordinary_sha256"]
                              and protocol.digest(resource) == facts["resource_sha256"]
                              and transaction_pending == pending_ref
                              and transaction_owned == facts["owned"],
                              "NATIVE_CLOSEOUT_TRANSACTION_PREIMAGE_CHANGED")
                self.db.execute("CREATE TABLE IF NOT EXISTS word_restricted_native_cua_closeouts "
                                "(ticket TEXT PRIMARY KEY, receipt TEXT NOT NULL)")
                columns = sorted(ROW_FIELDS)
                where = " AND ".join(name + " IS ?" for name in columns)
                changed = self.db.execute("UPDATE word_restricted_tickets SET status=? WHERE " + where,
                                          [TERMINAL] + [facts["row"][name] for name in columns])
                protocol.need(changed.rowcount == 1, "EXACT_RESTRICTED_CLOSEOUT_CAS_FAILED")
                self.db.execute("INSERT INTO word_restricted_native_cua_closeouts VALUES(?,?)",
                                (contract["ticket"], json.dumps(receipt, ensure_ascii=False, sort_keys=True)))
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            return self.finish(receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "apply"))
    parser.add_argument("--contract", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    broker = None
    try:
        ref = protocol.pin(args.contract)
        protocol.need(ref["sha256"] == args.contract_sha256, "EXACT_CLOSEOUT_CONTRACT_SHA_REQUIRED")
        broker = NativeCuaCloseout(args.root, readonly=args.command == "preview")
        if args.command == "apply":
            protocol.need(bool(args.plan_sha256), "REVIEWED_CLOSEOUT_PLAN_REQUIRED")
            result = broker.apply(ref, args.plan_sha256)
        else:
            result = broker.preview(ref)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps(dict(outcome="NATIVE_CUA_CLOSEOUT_REFUSED", reason=str(error),
                              error_type=type(error).__name__, input_operations=0,
                              native_retry_budget=0, ordinary_released=False), ensure_ascii=False))
        return 2
    finally:
        if broker is not None:
            broker.close()


if __name__ == "__main__":
    raise SystemExit(main())

