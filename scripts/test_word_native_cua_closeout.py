"""Exercise native proof, exact ticket CAS and crash recovery without Word input."""
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import word_native_cua_closeout as closeout
import word_restricted_broker as protocol

ACTOR = {"workspace_uuid": "26e665ba-7e98-49de-b0ea-dde37afbbb88",
         "surface_uuid": "a56e9c50-1af7-4d8c-8689-f92ff14d3bf4",
         "pane_uuid": "f77dea4d-7280-4a7e-95a1-14efa6b0e46b",
         "session_id": "01a187eb-8c5a-7313-a14f-ea326d081361"}


class NativeCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.root = self.home / "resources"
        self.root.mkdir()
        self.work = self.home / "native_cua_saved_close_v1"
        self.work.mkdir()
        self.pending = self.home / ".cache/wordrev/word-automation.pending.json"
        self.pending.parent.mkdir(parents=True)
        self.owned = self.home / "owned.docx"
        self.owned.write_bytes(b"unchanged owned stage")
        self.source = self.home / ("rollout-2026-10-30T00-00-00-" + ACTOR["session_id"] + ".jsonl")
        self.birth = int(time.time()) - 100
        self.db = sqlite3.connect(self.root / "queue.sqlite3", isolation_level=None)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE word_restricted_tickets(
              id TEXT PRIMARY KEY, disposition TEXT, plan TEXT, status TEXT,
              requested REAL, token TEXT, expires REAL, nonce TEXT, parent_pid INTEGER,
              pgid INTEGER, invocations INTEGER, result TEXT);
            CREATE TABLE word_restricted_revision_dispositions(
              id TEXT PRIMARY KEY, original_ticket TEXT, job_sha256 TEXT, contract TEXT, applied REAL);
            CREATE TABLE tickets(id TEXT PRIMARY KEY, resource TEXT, status TEXT, invocations INTEGER);
            CREATE TABLE resources(name TEXT PRIMARY KEY, external_locks TEXT);
            INSERT INTO tickets VALUES('ordinary', 'word-zotero', 'ACTIVE', 2);
        """)
        self.locks = {}
        for name in ("word.lock", "focus.lock", "queue.lock"):
            path = self.root / name
            path.touch()
            info = path.stat()
            self.locks[str(path)] = [info.st_dev, info.st_ino]
        self.db.execute("INSERT INTO resources VALUES(?,?)",
                        ("word-zotero", json.dumps(list(self.locks))))
        job = self.file("job.json", {"task": "original"})
        broker_pin = self.file("original-broker.py", {"source": "original"})
        self.old_plan = {"operation": "OWNED_REVISION_ROUNDTRIP", "initial_job": job,
                         "input": protocol.pin(self.owned), "evidence_root": str(self.work / "restricted-native"),
                         "controller_source": str(self.home),
                         "controller_pins": {"word_restricted_broker.py": broker_pin},
                         "service_pins": {}, "validator_pins": {}}
        plan_pin = self.file("old-plan.json", self.old_plan)
        original = self.file("original-contract.json", {"original_ticket": "ordinary", "initial_job": job})
        self.parent = {"id": "parent", "original_ticket": "ordinary", "job_sha256": job["sha256"],
                       "contract": json.dumps(original), "applied": 1.0}
        self.db.execute("INSERT INTO word_restricted_revision_dispositions VALUES(?,?,?,?,?)",
                        list(self.parent.values()))
        failure = {"exit_code": 2, "process_group_empty": True, "stdout": "",
                   "stderr": json.dumps({"status": "RESTRICTED_NATIVE_PENDING",
                     "reason": "OWNED_REVISION_NATIVE_STAGE_UNCONFIRMED", "error_type": "ResourceBusy",
                     "no_retry": True, "no_foreign_save_or_close": True, "legacy_released": False})}
        self.row = {"id": "restricted", "disposition": "parent", "plan": json.dumps(plan_pin),
                    "status": "PENDING", "requested": 1.0, "token": "original-token", "expires": 2.0,
                    "nonce": "original-nonce", "parent_pid": 9999990, "pgid": 9999991,
                    "invocations": 1, "result": json.dumps(failure)}
        self.db.execute("INSERT INTO word_restricted_tickets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        list(self.row.values()))
        self.pending.write_text(json.dumps({"owner": "restricted-revision:restricted", "pid": 9999991,
            "owned_document": str(self.owned), "status": "UNRESOLVED_NATIVE_OPERATION",
            "retry_permitted": False, "evidence_dir": str(self.work / "restricted-native")}))
        self.pending_ref, self.pending_bytes = closeout.observe_file(self.pending)
        self.baseline = {"restricted": deepcopy(self.row), "pending": self.pending_ref,
                         "owned": closeout.observe_file(self.owned)[0],
                         "ordinary": self.ordinary(), "resource": self.resource()}
        self.held = {"baseline": self.baseline, "locks": self.locks,
                     "fds": {"owned_open": True, "returncode": 0}}
        held = self.file("held.json", self.held)
        post = self.file("post.json", {"state": self.baseline,
            "owned_fds": {"owned_open": False, "returncode": 1, "stdout": "", "stderr": ""},
            "word_birth_observation": {"stdout": "13534 " + time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(self.birth))}})
        action = self.file("action.json", {"actor_session": ACTOR["session_id"], "original_ticket": "restricted",
            "close_budget": 1, "no_retry": True, "held_record": held})
        phase = self.file("phase.json", {"consumed": True})
        self.result = {"schema": "cmaverse-current-native-cua-saved-close-result-v1",
                       "actor_session": ACTOR["session_id"], "word_pid": 13534,
                       "held_guard": held, "independent_post": post, "action_slot": action,
                       "phase_slot": phase, "original_action_budget_remaining": 0,
                       "original_phase_budget_remaining": 0, "old_helper_exit": {"code": 2},
                       "queue_mutations": 0, "release_operations": 0}
        self.native_records = []
        for number in range(24):
            call = {"type": "function_call", "call_id": "call_" + str(number),
                    "namespace": "mcp__cua_repl", "name": "js", "arguments": json.dumps({"code": "read-only"})}
            output = "read-only observation"
            if number == 0:
                output = "{ documentMenuCount: 1, exactSavedOwnedDocument: true }"
            elif number == 1:
                call["arguments"] = json.dumps({"code": closeout.CLOSE_CODE})
                output = "Computer Use server error -10005: timeoutReached"
            elif number == 2:
                output = "{ openDocumentMenuEntries: 0, entries: [] }"
            self.native_records.extend([
                {"timestamp": "2026-10-30T00:00:00Z", "type": "response_item", "payload": call},
                {"timestamp": "2026-10-30T00:00:01Z", "type": "response_item",
                 "payload": {"type": "function_call_output", "call_id": call["call_id"], "output": output}}])
        self.rebuild_native()
        candidate = self.file("candidate.json", self.old_plan)
        proposal = self.file("proposal.json", {"original_applied_disposition": self.parent,
            "original_contract": original, "current_plan": candidate, "controller_broker_source": broker_pin})
        self.disposition = {"schema": protocol.NATIVE_REVISION_SCHEMA,
                            "successor": ACTOR, "approved_plan": {"path": str(self.source)},
                            "native_approval_chain": proposal}
        self.archive = self.work / "ARCHIVED_ORIGINAL_WORD_PENDING.json"
        self.contract = {"schema": closeout.SCHEMA, "ticket": "restricted",
                         "close_result": self.result_ref, "pending_archive": str(self.archive)}
        self.ref = self.file("closeout-contract.json", self.contract)
        self.broker = closeout.NativeCuaCloseout(self.root)
        self.addCleanup(self.broker.close)
        patches = [mock.patch.object(self.broker, "contract", return_value=(self.contract, self.disposition)),
            mock.patch.object(Path, "home", return_value=self.home),
            mock.patch.object(protocol, "native_identity", return_value=ACTOR),
            mock.patch.object(closeout, "group_alive", return_value=False),
            mock.patch.object(closeout.identity, "process", return_value={"pid": 13534, "birth": [self.birth, 0],
                "executable": "/Applications/Microsoft Word.app/Contents/MacOS/Microsoft Word",
                "file_identity": [1, 2, 3, 4]}),
            mock.patch.object(closeout.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="")),
            mock.patch.object(closeout, "sys", SimpleNamespace(flags=SimpleNamespace(ignore_environment=True), dont_write_bytecode=True))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def file(self, name, value):
        path = self.home / name
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n")
        return protocol.pin(path)

    def ordinary(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM tickets ORDER BY id")]

    def resource(self):
        return dict(self.db.execute("SELECT * FROM resources").fetchone())

    def rebuild_native(self):
        metadata = {"type": "session_meta", "payload": {"id": ACTOR["session_id"]}}
        raw = (json.dumps(metadata) + "\n").encode()
        items = []
        for index, record in enumerate(self.native_records):
            line = (json.dumps(record) + "\n").encode()
            item = self.file("native-" + str(index) + ".jsonl", record)
            item.update(source=str(self.source), offset=len(raw), timestamp=record["timestamp"],
                        type=record["payload"]["type"], call_id=record["payload"]["call_id"])
            items.append(item)
            raw += line
        self.source.write_bytes(raw)
        self.result.update(native_proof=self.file("manifest.json", {"source": str(self.source),
            "inode": self.source.stat().st_ino, "records": items}),
            native_pre=items[1], native_post=items[5])
        self.result_ref = self.file("result.json", self.result)
        if hasattr(self, "contract"):
            self.contract["close_result"] = self.result_ref

    def assert_unchanged(self):
        self.assertEqual(self.broker.row("restricted"), self.row)
        self.assertEqual(self.pending.read_bytes(), self.pending_bytes)
        self.assertEqual(self.ordinary(), self.baseline["ordinary"])
        self.assertEqual(self.resource(), self.baseline["resource"])

    def test_complete_native_proof_is_read_without_application_calls(self):
        _, _, evidence = closeout.native_result(self.result_ref, ACTOR, source_path=str(self.source))
        self.assertEqual(evidence["native_pairs"], 24)
        closeout.subprocess.run.assert_not_called()

    def test_preview_changes_no_queue_pending_or_archive(self):
        self.assertEqual(self.broker.preview(self.ref)["native_retry_budget"], 0)
        self.assert_unchanged()
        self.assertFalse(self.archive.exists())

    def test_original_child_pending_directory_is_the_exact_plan_evidence_root(self):
        self.assertEqual(json.loads(self.pending_bytes)["evidence_dir"], self.old_plan["evidence_root"])
        self.assertEqual(self.broker.preview(self.ref)["native_retry_budget"], 0)
        changed = dict(self.old_plan, evidence_root=str(self.work))
        replacement = self.file("old-plan-wrong-root.json", changed)
        self.db.execute("UPDATE word_restricted_tickets SET plan=?", (json.dumps(replacement),))
        self.baseline["restricted"]["plan"] = json.dumps(replacement)
        self.held["baseline"] = self.baseline
        self.result["held_guard"] = self.file("held.json", self.held)
        post = json.loads(Path(self.result["independent_post"]["path"]).read_bytes())
        post["state"] = self.baseline
        self.result["independent_post"] = self.file("post.json", post)
        action = json.loads(Path(self.result["action_slot"]["path"]).read_bytes())
        action["held_record"] = self.result["held_guard"]
        self.result["action_slot"] = self.file("action.json", action)
        self.result_ref = self.file("result.json", self.result)
        self.contract["close_result"] = self.result_ref
        with self.assertRaisesRegex(protocol.ResourceBusy, "EXACT_ORIGINAL_NATIVE_STAGE_CHANGED"):
            self.broker.preview(self.ref)

    def test_apply_preserves_failure_invocation_owner_and_archives_exact_pending(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        result = self.broker.apply(self.ref, sha)
        self.assertEqual(result["status"], closeout.TERMINAL)
        self.assertEqual(self.broker.row("restricted"), dict(self.row, status=closeout.TERMINAL))
        self.assertEqual(self.archive.read_bytes(), self.pending_bytes)
        self.assertFalse(self.archive.stat().st_mode & 0o222)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.ordinary(), self.baseline["ordinary"])
        self.assertEqual(self.resource(), self.baseline["resource"])
        self.assertEqual(result["native_invocations_added"], 0)

    def test_completed_closeout_reuses_one_receipt_without_another_native_invocation(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        self.broker.apply(self.ref, sha)
        self.broker.apply(self.ref, sha)
        self.assertEqual(self.db.execute("SELECT count(*) FROM word_restricted_native_cua_closeouts").fetchone()[0], 1)
        self.assertEqual(self.broker.row("restricted")["invocations"], 1)

    def test_crash_after_commit_resumes_exact_pending_retirement(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        with mock.patch.object(self.broker, "finish", side_effect=OSError("retirement interrupted")):
            with self.assertRaises(OSError):
                self.broker.apply(self.ref, sha)
        self.assertTrue(self.pending.exists())
        self.assertEqual(self.broker.row("restricted")["status"], closeout.TERMINAL)
        self.broker.apply(self.ref, sha)
        self.assertFalse(self.pending.exists())

    def test_wrong_actor_is_refused_before_pending_archival(self):
        with mock.patch.object(protocol, "native_identity", return_value=dict(ACTOR, session_id="foreign")):
            with self.assertRaises(protocol.ResourceBusy):
                self.broker.apply(self.ref, "wrong")
        self.assert_unchanged()
        self.assertFalse(self.archive.exists())

    def test_stale_preview_sha_cannot_close_or_archive(self):
        with self.assertRaises(protocol.ResourceBusy):
            self.broker.apply(self.ref, "wrong")
        self.assert_unchanged()

    def test_live_or_unknown_original_group_prevents_closeout(self):
        with mock.patch.object(closeout, "group_alive", return_value=True):
            with self.assertRaises(protocol.ResourceBusy):
                self.broker.preview(self.ref)
        self.assert_unchanged()

    def test_unknown_or_open_word_fd_prevents_closeout(self):
        for result in (SimpleNamespace(returncode=0, stdout="open", stderr=""),
                       SimpleNamespace(returncode=1, stdout="", stderr="warning")):
            with self.subTest(result=result), mock.patch.object(closeout.subprocess, "run", return_value=result):
                with self.assertRaises(protocol.ResourceBusy):
                    self.broker.preview(self.ref)
        self.assert_unchanged()

    def test_current_word_process_timeout_does_not_mutate_queue(self):
        with mock.patch.object(closeout.identity, "process", side_effect=subprocess.TimeoutExpired("process", 0)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.broker.preview(self.ref)
        self.assert_unchanged()

    def test_original_word_birth_change_prevents_closeout(self):
        closeout.identity.process.return_value["birth"][0] += 1
        with self.assertRaises(protocol.ResourceBusy):
            self.broker.preview(self.ref)
        self.assert_unchanged()

    def test_parent_change_during_archival_is_preserved_and_cas_refused(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        archive = closeout.archived_file
        def change(*args):
            value = archive(*args)
            self.db.execute("UPDATE word_restricted_revision_dispositions SET job_sha256='foreign'")
            return value
        with mock.patch.object(closeout, "archived_file", side_effect=change):
            with self.assertRaises(ValueError):
                self.broker.apply(self.ref, sha)
        self.assert_unchanged()
        self.assertEqual(self.db.execute("SELECT job_sha256 FROM word_restricted_revision_dispositions").fetchone()[0], "foreign")

    def test_owned_file_change_during_archival_cannot_commit(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        archive = closeout.archived_file
        def change(*args):
            value = archive(*args)
            self.owned.write_bytes(b"changed during archival")
            return value
        with mock.patch.object(closeout, "archived_file", side_effect=change):
            with self.assertRaises(protocol.ResourceBusy):
                self.broker.apply(self.ref, sha)
        self.assert_unchanged()

    def test_ordinary_owner_change_during_archival_is_not_overwritten(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        archive = closeout.archived_file
        def change(*args):
            value = archive(*args)
            self.db.execute("UPDATE tickets SET status='FOREIGN_CHANGE'")
            return value
        with mock.patch.object(closeout, "archived_file", side_effect=change):
            with self.assertRaises(protocol.ResourceBusy):
                self.broker.apply(self.ref, sha)
        self.assertEqual(self.broker.row("restricted"), self.row)
        self.assertEqual(self.pending.read_bytes(), self.pending_bytes)
        self.assertEqual(self.ordinary()[0]["status"], "FOREIGN_CHANGE")

    def test_held_lock_prevents_closeout(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        path = Path(next(iter(self.locks)))
        with path.open("r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.broker.apply(self.ref, sha)
        self.assert_unchanged()

    def test_lock_replaced_while_acquiring_is_refused(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        original = fcntl.flock
        def change(fd, flags):
            original(fd, flags)
            path = Path(sorted(self.locks)[0])
            replacement = path.with_suffix(".new")
            replacement.touch()
            replacement.replace(path)
        with mock.patch.object(closeout.fcntl, "flock", side_effect=change):
            with self.assertRaises(protocol.ResourceBusy):
                self.broker.apply(self.ref, sha)
        self.assert_unchanged()

    def test_changed_archive_refuses_recovery_after_committed_closeout(self):
        sha = self.broker.preview(self.ref)["plan_sha256"]
        with mock.patch.object(self.broker, "finish", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.broker.apply(self.ref, sha)
        self.archive.chmod(0o644)
        with self.assertRaises(protocol.ResourceBusy):
            self.broker.apply(self.ref, sha)
        self.assertEqual(self.pending.read_bytes(), self.pending_bytes)

    def test_native_output_echo_from_other_tool_does_not_prove_inventory(self):
        self.native_records[0]["payload"]["namespace"] = "functions"
        self.rebuild_native()
        with self.assertRaises(protocol.ResourceBusy):
            closeout.native_result(self.result_ref, ACTOR, source_path=str(self.source))

    def test_second_native_close_is_refused(self):
        self.native_records[6]["payload"]["arguments"] = json.dumps({"code": closeout.CLOSE_CODE})
        self.rebuild_native()
        with self.assertRaises(protocol.ResourceBusy):
            closeout.native_result(self.result_ref, ACTOR, source_path=str(self.source))

    def test_declared_pre_reference_cannot_be_substituted(self):
        self.result["native_pre"] = deepcopy(self.result["native_post"])
        self.result_ref = self.file("result.json", self.result)
        with self.assertRaises(protocol.ResourceBusy):
            closeout.native_result(self.result_ref, ACTOR, source_path=str(self.source))

    def test_other_rollout_with_the_same_session_name_is_refused(self):
        with self.assertRaises(protocol.ResourceBusy):
            closeout.native_result(self.result_ref, ACTOR, source_path=str(self.source) + ".other")

    def test_event_echo_cannot_replace_native_response_record(self):
        self.native_records[1]["type"] = "event_msg"
        self.rebuild_native()
        with self.assertRaises(protocol.ResourceBusy):
            closeout.native_result(self.result_ref, ACTOR, source_path=str(self.source))


if __name__ == "__main__":
    unittest.main()
