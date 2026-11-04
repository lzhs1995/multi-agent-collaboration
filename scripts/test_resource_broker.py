import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from resource_broker import Broker, ResourceBusy, os_lock


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Broker(self.tmp.name)
        self.addCleanup(self.b.db.close)
        self.b.configure("shared")

    def request(self, task):
        return self.b.request("shared", task, str(uuid.uuid4()), str(uuid.uuid4()))

    def test_cross_workspace_queue_survives_coordinator_exit(self):
        a, b = self.request("a"), self.request("b")
        with self.assertRaisesRegex(ResourceBusy, "WAIT_YOUR_TURN"):
            self.b.grant(b["id"])
        lease = self.b.grant(a["id"])
        other = Broker(self.tmp.name)
        self.addCleanup(other.db.close)
        with self.assertRaisesRegex(ResourceBusy, "BUSY"):
            other.grant(b["id"])
        released = other.release(a["id"], lease["token"], {"pending": False, "own_processes_empty": True, "documents": "UNKNOWN"})
        self.assertFalse(released["release_proof"]["documents_verified"])
        self.assertEqual(other.grant(b["id"])["status"], "ACTIVE")

    def test_no_silent_takeover_on_expiry(self):
        a = self.request("a")
        lease = self.b.grant(a["id"])
        self.b.db.execute("UPDATE tickets SET expires=? WHERE id=?", (time.time() - 1, a["id"]))
        b = self.request("b")
        with self.assertRaisesRegex(ResourceBusy, "RECONCILIATION"):
            self.b.grant(b["id"])
        with self.assertRaisesRegex(ResourceBusy, "RECONCILIATION"):
            self.b.release(a["id"], lease["token"], {"pending": False, "own_processes_empty": True})
        self.b.release(a["id"], lease["token"], {"pending": False, "own_processes_empty": True}, reconcile=True)
        self.assertEqual(self.b.grant(b["id"])["status"], "ACTIVE")

    def test_actual_os_lock_and_pending_block_release(self):
        a = self.request("a")
        lease = self.b.grant(a["id"])
        with os_lock(self.b.lock_path("shared")):
            with self.assertRaisesRegex(ResourceBusy, "OS_LOCK_BUSY"):
                self.b.release(a["id"], lease["token"], {"pending": False, "own_processes_empty": True})
        with self.assertRaisesRegex(ResourceBusy, "DRAIN_REQUIRED"):
            self.b.release(a["id"], lease["token"], {"pending": True, "own_processes_empty": True})

    def test_run_records_real_child_group_and_release(self):
        a = self.request("a")
        lease = self.b.grant(a["id"])
        result = self.b.run(a["id"], lease["token"], [sys.executable, "-c", "print('fixture complete')"], text=True, stdout=subprocess.PIPE)
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["process_group_empty"])
        self.assertIn("fixture complete", result["stdout"])
        self.b.release(a["id"], lease["token"], {"pending": False, "own_processes_empty": True})

    def test_external_lock_busy_and_mapping_pinned(self):
        p = Path(self.tmp.name) / "external.lock"
        self.b.configure("word", [p])
        with self.assertRaisesRegex(ResourceBusy, "PINNED"):
            self.b.configure("word", [])
        ticket = self.b.request("word", "a", str(uuid.uuid4()), str(uuid.uuid4()))
        with os_lock(p):
            with self.assertRaisesRegex(ResourceBusy, "OS_LOCK_BUSY"):
                self.b.grant(ticket["id"])

    def test_word_unknown_nonzero_and_missing_observation_fail_closed(self):
        self.b.configure("word-zotero")
        ticket = self.b.request("word-zotero", "word-task", str(uuid.uuid4()), str(uuid.uuid4()))
        lease = self.b.grant(ticket["id"])
        proof = {"pending": False, "own_processes_empty": True}
        with self.assertRaisesRegex(ResourceBusy, "RECEIPT_REQUIRED"):
            self.b.release(ticket["id"], lease["token"], proof)
        p = Path(self.tmp.name) / "observed.json"
        raw = {"task_id": "word-task", "documents": 0, "windows": 0, "modal": False,
               "zotero_current_doc": False, "zotero_current_window": False, "pending": False}
        for count in (None, 1, 0):
            p.write_text(json.dumps({**raw, "documents": count}))
            proof["observed_receipt"] = {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            if count != 0:
                with self.assertRaisesRegex(ResourceBusy, "CONDITION_FAILED"):
                    self.b.release(ticket["id"], lease["token"], proof)
            else:
                self.assertEqual(self.b.release(ticket["id"], lease["token"], proof)["status"], "RELEASED")

    def test_nlm_empty_list_is_not_remote_completion(self):
        self.b.configure("nlm-account:test")
        ticket = self.b.request("nlm-account:test", "n", str(uuid.uuid4()), str(uuid.uuid4()))
        lease = self.b.grant(ticket["id"])
        proof = {"pending": False, "own_processes_empty": True}
        for requests in (None, ["still-running"], []):
            with self.assertRaises(ResourceBusy):
                self.b.release(ticket["id"], lease["token"], {**proof, "in_flight_requests": requests})
        self.assertEqual(self.b.release(ticket["id"], lease["token"], {**proof, "in_flight_requests": [], "no_request_submitted": True})["status"], "RELEASED")

    def test_abandoned_waiting_owner_can_withdraw(self):
        first, second = self.request("a"), self.request("b")
        with self.assertRaisesRegex(ResourceBusy, "OWNER"):
            self.b.withdraw(first["id"], "intruder", first["workspace_uuid"], first["surface_uuid"])
        self.b.withdraw(first["id"], "a", first["workspace_uuid"], first["surface_uuid"])
        self.assertEqual(self.b.grant(second["id"])["status"], "ACTIVE")


class RestrictedWordGrantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Broker(self.tmp.name)
        self.addCleanup(self.b.db.close)
        self.b.configure("word-zotero")
        self.ticket = self.b.request("word-zotero", "new-word-task", str(uuid.uuid4()), str(uuid.uuid4()))

    def restricted_table(self):
        # Exact existing table shape: this table is exclusively word-zotero;
        # it has no resource column and is not created by the ordinary broker.
        self.b.db.executescript("""
            CREATE TABLE word_restricted_tickets (
                id TEXT PRIMARY KEY, disposition TEXT NOT NULL, plan TEXT NOT NULL,
                status TEXT NOT NULL, requested REAL NOT NULL, token TEXT, expires REAL,
                nonce TEXT, parent_pid INTEGER, pgid INTEGER,
                invocations INTEGER NOT NULL DEFAULT 0, result TEXT);
            CREATE UNIQUE INDEX one_restricted_word_writer
                ON word_restricted_tickets((1)) WHERE status IN ('ACTIVE','PENDING');
        """)

    def restricted_ticket(self, status, *, expires=None):
        identity = str(uuid.uuid4())
        self.b.db.execute(
            "INSERT INTO word_restricted_tickets"
            "(id,disposition,plan,status,requested,token,expires,nonce,invocations,result)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (identity, "original-disposition", '{"original_plan":true}', status,
             time.time(), "original-token", expires, "original-nonce", 1, '{"preserved":true}'))
        return identity

    def assert_restricted_blocks(self, status, expires):
        self.restricted_table()
        self.restricted_ticket(status, expires=expires)
        before = [dict(row) for row in self.b.db.execute("SELECT * FROM word_restricted_tickets")]
        self.assertIsNone(self.b.db.execute("SELECT 1 FROM tickets WHERE status='ACTIVE'").fetchone())
        with self.assertRaisesRegex(ResourceBusy, "RESTRICTED_WORD_UNRESOLVED"):
            self.b.grant(self.ticket["id"])
        self.assertEqual(self.b.get(self.ticket["id"]), self.ticket)
        self.assertEqual([dict(row) for row in self.b.db.execute("SELECT * FROM word_restricted_tickets")], before)

    def test_pending_blocks_when_ordinary_active_is_empty(self):
        self.assert_restricted_blocks("PENDING", time.time() + 600)

    def test_active_blocks_when_ordinary_active_is_empty(self):
        self.assert_restricted_blocks("ACTIVE", time.time() + 600)

    def test_expired_pending_still_requires_original_reconciliation(self):
        self.assert_restricted_blocks("PENDING", time.time() - 600)

    def test_absent_restricted_table_remains_backward_compatible(self):
        self.assertEqual(self.b.grant(self.ticket["id"])["status"], "ACTIVE")
        self.assertIsNone(self.b.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='word_restricted_tickets'"
        ).fetchone())

    def test_terminal_restricted_tickets_do_not_block_or_change_fifo(self):
        self.restricted_table()
        for status in ("COMPLETE_WITH_FOREIGN_UNKNOWN", "FAILED_PREINPUT", "FAILED_NATIVE_CLOSED"):
            self.restricted_ticket(status)
        before = [dict(row) for row in self.b.db.execute("SELECT * FROM word_restricted_tickets")]
        second = self.b.request("word-zotero", "later-task", str(uuid.uuid4()), str(uuid.uuid4()))
        with self.assertRaisesRegex(ResourceBusy, "WAIT_YOUR_TURN"):
            self.b.grant(second["id"])
        self.assertEqual(self.b.grant(self.ticket["id"])["status"], "ACTIVE")
        self.assertEqual([dict(row) for row in self.b.db.execute("SELECT * FROM word_restricted_tickets")], before)

    def test_restricted_word_does_not_block_another_resource(self):
        self.restricted_table()
        self.restricted_ticket("PENDING")
        self.b.configure("nlm-account:test")
        ticket = self.b.request("nlm-account:test", "nlm-task", str(uuid.uuid4()), str(uuid.uuid4()))
        self.assertEqual(self.b.grant(ticket["id"])["status"], "ACTIVE")
        self.assertEqual(self.b.get(self.ticket["id"]), self.ticket)

    def test_terminal_restricted_rows_do_not_bypass_original_os_lock(self):
        self.restricted_table()
        self.restricted_ticket("COMPLETE_WITH_FOREIGN_UNKNOWN")
        with os_lock(self.b.lock_path("word-zotero")):
            with self.assertRaisesRegex(ResourceBusy, "OS_LOCK_BUSY"):
                self.b.grant(self.ticket["id"])
        self.assertEqual(self.b.get(self.ticket["id"]), self.ticket)

    def test_restricted_check_and_grant_share_write_transaction(self):
        self.restricted_table()
        identity = self.restricted_ticket("COMPLETE_WITH_FOREIGN_UNKNOWN")
        # A second real SQLite connection tries to introduce PENDING at both
        # the lane check and the ordinary grant. BEGIN IMMEDIATE must exclude
        # that writer throughout, without a check-then-grant race window.
        contender = sqlite3.connect(Path(self.tmp.name) / "queue.sqlite3", timeout=0, isolation_level=None)
        self.addCleanup(contender.close)
        observed = []

        def trace(sql):
            phase = ("lane_check" if "FROM word_restricted_tickets" in sql
                     else "grant" if sql.startswith("UPDATE tickets SET status='ACTIVE'") else None)
            if phase is None:
                return
            outcome = "WRITE_SUCCEEDED"
            try:
                contender.execute("UPDATE word_restricted_tickets SET status='PENDING' WHERE id=?", (identity,))
            except sqlite3.OperationalError as error:
                outcome = str(error)
            observed.append((phase, self.b.db.in_transaction, outcome))

        self.b.db.set_trace_callback(trace)
        try:
            lease = self.b.grant(self.ticket["id"])
        finally:
            self.b.db.set_trace_callback(None)
        self.assertEqual(lease["status"], "ACTIVE")
        self.assertEqual(observed, [("lane_check", True, "database is locked"),
                                    ("grant", True, "database is locked")])
        self.assertEqual(self.b.db.execute("SELECT status FROM word_restricted_tickets WHERE id=?",
                                          (identity,)).fetchone()[0], "COMPLETE_WITH_FOREIGN_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
