import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from collaboration_review_budget import Ledger, BudgetError


class ReviewBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.seq = 0
        self.auth = self.ref('authority.txt', b'explicit owner decision for synthetic new project')
        self.standard = self.ref('standard.txt', b'frozen synthetic review standard')
        self.artifact = self.ref('input.txt', b'revision one')
        self.db = self.root / 'budget.sqlite3'
        self.ledger = Ledger.create(self.db, project_id='new-project', owner='original-owner',
                                    authorization=self.auth, new_tasks_only=True)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def ref(self, name, data):
        p = self.root / name
        p.write_bytes(data)
        return dict(path=str(p), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def apply(self, action, **fields):
        self.seq += 1
        return self.ledger.apply(dict(operation_id=f'op-{self.seq}', action=action, **fields))

    def register(self, scope='chapter', lineage='chapter-issues', roles=None, **extra):
        data = dict(scope_id=scope, issue_lineage=lineage, owner='original-owner', standard=self.standard,
                    required_reviews=roles or [{'reviewer_role': 'methods', 'review_scope': 'method validity'}],
                    max_attempts_per_review=2, **extra)
        return self.apply('register_scope', **data, authorization=self.decision('register_scope', data))

    def decision(self, action, binding):
        value = dict(schema='review-owner-decision-v1', project_id='new-project',
                     owner='original-owner', action=action, binding=binding)
        self.seq += 1
        return self.ref(f'decision-{self.seq}.json', json.dumps(value).encode())

    def readback(self, **changes):
        value = dict(schema='review-attempt-readback-v1', project_id='new-project',
                     round_id='r1', attempt_id='r1-methods', outcome='COMPLETE',
                     verdict='NEEDS_CHANGES')
        value.update(changes)
        self.seq += 1
        return self.ref(f'readback-{self.seq}.json', json.dumps(value).encode())

    def retry(self, previous='r1-methods', next_id='retry-methods'):
        binding = dict(scope_id='chapter', issue_lineage='chapter-issues', attempt_id=previous, new_attempt_id=next_id)
        return self.apply('retry', attempt_id=previous, new_attempt_id=next_id,
                          actor={'owner': 'successor', 'model': 'another', 'route': 'herdr'},
                          authorization=self.decision('retry', binding), authorized_by='original-owner')

    def begin(self, rid='r1', scope='chapter', artifact=None, roles=('methods',), **actor):
        return self.apply('begin_round', scope_id=scope, round_id=rid, artifact=artifact or self.artifact,
                          attempts=[dict(attempt_id=rid + '-' + role, reviewer_role=role,
                                         actor=dict(owner=actor.get('owner', 'chapter-owner'),
                                                    model=actor.get('model', 'same-model'),
                                                    route=actor.get('route', 'cmux')))
                                    for role in roles])

    def complete(self, attempt='r1-methods', verdict='NEEDS_CHANGES', **extra):
        evidence = self.ref(attempt + '.json', json.dumps({'original_round_or_nlm_result': attempt}).encode())
        return self.apply('settle', attempt_id=attempt, outcome='COMPLETE', verdict=verdict,
                          evidence=[evidence], usage={'tokens': 11}, **extra)

    def test_original_intent_survives_restart_and_completion_is_counted_once(self):
        self.register()
        self.begin()
        self.ledger.close()
        self.ledger = Ledger(self.db, project_id='new-project')
        current = self.ledger.status('chapter')
        self.assertEqual(current['completed_rounds'], 0)
        self.assertEqual(current['rounds'][0]['attempts'][0]['status'], 'PENDING')
        self.complete()
        current = self.ledger.status('chapter')
        self.assertEqual(current['completed_rounds'], 1)
        self.assertEqual(current['rounds'][0]['status'], 'COMPLETE')
        self.assertFalse(current['domain_acceptance_proven'])

    def test_unknown_attempt_blocks_new_round_until_original_readback(self):
        self.register()
        self.begin()
        error = self.ref('error.json', b'{"transport":"response unknown"}')
        self.apply('mark_unknown', attempt_id='r1-methods', reason='lost response',
                   evidence=[error], usage={'tokens': 8})
        self.ledger.close()
        self.ledger = Ledger(self.db, project_id='new-project')
        with self.assertRaisesRegex(BudgetError, 'ORIGINAL_ATTEMPT_UNRESOLVED'):
            self.begin('new-nonce', owner='new-owner', route='herdr')
        with self.assertRaisesRegex(BudgetError, 'ORIGINAL_READBACK_REQUIRED'):
            self.complete()
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 0)
        self.complete(reconciliation=self.readback())
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 1)

    def test_unknown_readback_must_bind_original_project_attempt_round_and_outcome(self):
        self.register()
        self.begin()
        self.apply('mark_unknown', attempt_id='r1-methods', reason='transport unknown',
                   evidence=[self.ref('unknown-result', b'unknown')], usage={'tokens': 8})
        with self.assertRaisesRegex(BudgetError, 'READBACK_JSON_REQUIRED'):
            self.complete(reconciliation=self.ref('unrelated', b'unrelated file'))
        for changes in ({'project_id': 'another'}, {'attempt_id': 'other-attempt'},
                        {'round_id': 'other-round'}, {'outcome': 'INCOMPLETE'}, {'verdict': 'PASS'}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(BudgetError, 'READBACK_BINDING_MISMATCH'):
                    self.complete(reconciliation=self.readback(**changes))
                self.assertEqual(self.ledger.status('chapter')['rounds'][0]['attempts'][0]['status'], 'UNKNOWN')
        self.apply('settle', attempt_id='r1-methods', outcome='INCOMPLETE', verdict=None,
                   evidence=[self.ref('original-failure', b'original observed failure')], usage={'tokens': 9},
                   reconciliation=self.readback(outcome='INCOMPLETE', verdict=None))
        self.retry()
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 0)

    def test_incomplete_cost_is_retained_and_retry_requires_explicit_bounded_decision(self):
        self.register()
        self.begin()
        error = self.ref('failed.json', b'{"original_attempt_failed":true}')
        self.apply('settle', attempt_id='r1-methods', outcome='INCOMPLETE', verdict=None,
                   evidence=[error], usage={'tokens': 7})
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 0)
        with self.assertRaisesRegex(BudgetError, 'ORIGINAL_ATTEMPT_UNRESOLVED'):
            self.begin('r2')
        retry = dict(attempt_id='r1-methods', new_attempt_id='retry-methods',
                     actor={'owner': 'successor', 'model': 'another', 'route': 'herdr'})
        with self.assertRaises(BudgetError):
            self.apply('retry', **retry)
        self.retry()
        self.complete('retry-methods')
        current = self.ledger.status('chapter')
        self.assertEqual(current['completed_rounds'], 1)
        self.assertEqual([a['usage']['tokens'] for a in current['rounds'][0]['attempts']], [7, 11])

    def test_same_review_key_reuses_verdict_across_owner_model_route_without_round(self):
        self.register()
        self.begin()
        self.complete()
        result = self.begin('another', owner='new', model='different', route='herdr')
        self.assertEqual(result['status'], 'REUSE')
        self.assertEqual(result['verdicts'][0]['verdict'], 'NEEDS_CHANGES')
        self.assertEqual(len(self.ledger.status('chapter')['rounds']), 1)

    def test_changed_artifacts_and_aliases_share_three_round_ceiling(self):
        self.register()
        binding = dict(scope_id='chapter', issue_lineage='chapter-issues', alias='renamed-chapter')
        self.apply('alias_scope', **binding, authorized_by='original-owner',
                   authorization=self.decision('alias_scope', binding))
        for i in range(3):
            self.begin(f'r{i}', scope='renamed-chapter', artifact=self.ref(f'v{i}', f'revision {i}'.encode()))
            self.complete(f'r{i}-methods', verdict='FAIL')
        with self.assertRaisesRegex(BudgetError, 'REVIEW_BUDGET_EXHAUSTED'):
            self.begin('r4', artifact=self.ref('v4', b'new version'), owner='successor', route='herdr')
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 3)
        self.assertEqual(self.ledger.status('renamed-chapter')['completed_rounds'], 3)

    def test_scope_lineage_standard_and_reviewers_cannot_be_reregistered(self):
        self.register()
        with self.assertRaisesRegex(BudgetError, 'SCOPE_REGISTRATION_FROZEN'):
            self.register(lineage='new-lineage')
        with self.assertRaisesRegex(BudgetError, 'LINEAGE_ALREADY_REGISTERED'):
            self.register(scope='renamed')
        with self.assertRaisesRegex(BudgetError, 'SCOPE_REGISTRATION_FROZEN'):
            self.register(roles=[{'reviewer_role': 'new', 'review_scope': 'new'}])
        self.begin()
        with self.assertRaisesRegex(BudgetError, 'FROZEN_REVIEW_SCOPES_REQUIRED'):
            self.begin('escape', roles=('methods', 'new-specialist'))

    def test_old_generic_authorization_cannot_register_new_scope(self):
        data = dict(scope_id='chapter', issue_lineage='chapter-issues', owner='original-owner',
                    standard=self.standard, required_reviews=[{'reviewer_role': 'methods', 'review_scope': 'x'}],
                    max_attempts_per_review=2, authorization=self.auth)
        with self.assertRaisesRegex(BudgetError, 'OWNER_DECISION'):
            self.apply('register_scope', **data)

    def test_parallel_specialists_complete_one_round_and_failed_attempt_is_retained(self):
        self.register(roles=[{'reviewer_role': role, 'review_scope': role} for role in ('methods', 'sources')])
        self.begin(roles=('methods', 'sources'))
        self.complete('r1-sources')
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 0)
        self.apply('settle', attempt_id='r1-methods', outcome='INCOMPLETE', verdict=None,
                   evidence=[self.ref('fail.json', b'failed')], usage={'tokens': 4})
        self.retry()
        self.complete('retry-methods')
        status = self.ledger.status('chapter')
        self.assertEqual(status['completed_rounds'], 1)
        self.assertEqual(len(status['rounds'][0]['attempts']), 3)
        with self.assertRaisesRegex(BudgetError, 'ORIGINAL_INCOMPLETE_RESULT_REQUIRED'):
            self.retry(previous='r1-sources', next_id='source-rerun')

    def test_original_operation_is_idempotent_but_reusing_id_for_other_result_fails(self):
        self.register()
        request = dict(operation_id='fixed', action='begin_round', scope_id='chapter', round_id='r1',
                       artifact=self.artifact, attempts=[dict(attempt_id='a', reviewer_role='methods',
                                                            actor={'owner': 'o', 'model': 'm', 'route': 'cmux'})])
        self.ledger.apply(request)
        self.assertTrue(self.ledger.apply(request)['idempotent'])
        changed = dict(request, round_id='other')
        with self.assertRaisesRegex(BudgetError, 'OPERATION_ID_CONFLICT'):
            self.ledger.apply(changed)
        self.assertEqual(len(self.ledger.status('chapter')['rounds']), 1)

    def test_tampered_evidence_rolls_back_and_original_result_can_settle_after_restart(self):
        self.register()
        self.begin()
        bad = self.ref('result.json', b'original')
        Path(bad['path']).write_bytes(b'tampered')
        with self.assertRaises(BudgetError):
            self.apply('settle', attempt_id='r1-methods', outcome='COMPLETE', verdict='PASS',
                       evidence=[bad], usage={'tokens': 50})
        self.ledger.close()
        self.ledger = Ledger(self.db, project_id='new-project')
        self.assertEqual(self.ledger.status('chapter')['rounds'][0]['attempts'][0]['status'], 'PENDING')
        self.complete()
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 1)

    def test_concurrent_round_reservations_leave_only_one_durable_intent(self):
        self.register()
        barrier = Barrier(2)
        def worker(n):
            ledger = Ledger(self.db, project_id='new-project')
            barrier.wait()
            try:
                return ledger.apply(dict(operation_id=f'parallel-{n}', action='begin_round', scope_id='chapter',
                    round_id=f'r{n}', artifact=self.artifact, attempts=[dict(attempt_id=f'a{n}', reviewer_role='methods',
                        actor={'owner': 'o', 'model': 'm', 'route': 'cmux'})]))['status']
            except BudgetError as exc:
                return str(exc)
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(worker, range(2)))
        self.assertEqual(answers.count('INTENT_RECORDED'), 1)
        self.assertEqual(len(self.ledger.status('chapter')['rounds']), 1)

    def test_interrupted_sqlite_writer_rolls_back_and_releases_lock(self):
        self.register()
        code = "import sqlite3,sys,time; d=sqlite3.connect(sys.argv[1]); d.execute('BEGIN IMMEDIATE'); d.execute(\"UPDATE scopes SET lineage='uncommitted'\"); print('locked',flush=True); time.sleep(30)"
        process = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.db)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            with self.assertRaisesRegex(BudgetError, 'LEDGER_BUSY'):
                self.begin()
        finally:
            process.kill()
            process.wait(timeout=5)
            process.stdout.close()
        self.begin()
        self.assertEqual(self.ledger.status('chapter')['issue_lineage'], 'chapter-issues')

    def test_cli_inspects_existing_project_without_initializing_or_dispatching(self):
        self.register()
        script = Path(__file__).with_name('collaboration_review_budget.py')
        result = subprocess.run([sys.executable, '-B', str(script), '--db', str(self.db),
                                 '--project-id', 'new-project', 'status', '--scope-id', 'chapter'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['completed_rounds'], 0)

    def test_retry_budget_is_finite_and_unknown_cannot_request_retry(self):
        self.register()
        self.begin()
        with self.assertRaisesRegex(BudgetError, 'ORIGINAL_INCOMPLETE_RESULT_REQUIRED'):
            self.retry()
        self.apply('settle', attempt_id='r1-methods', outcome='INCOMPLETE', verdict=None,
                   evidence=[self.ref('fail1', b'first failed')], usage={'tokens': 3})
        self.retry()
        self.apply('settle', attempt_id='retry-methods', outcome='INCOMPLETE', verdict=None,
                   evidence=[self.ref('fail2', b'second failed')], usage={'tokens': 2})
        with self.assertRaisesRegex(BudgetError, 'ATTEMPT_BUDGET_EXHAUSTED'):
            self.retry(previous='retry-methods', next_id='third-attempt')
        status = self.ledger.status('chapter')
        self.assertTrue(status['unresolved'])
        self.assertEqual(status['completed_rounds'], 0)
        self.assertEqual(len(status['rounds'][0]['attempts']), 2)

    def test_usage_cannot_decrease_after_unknown_and_fresh_reuse_checks_original_evidence(self):
        self.register()
        self.begin()
        self.apply('mark_unknown', attempt_id='r1-methods', reason='transport unknown',
                   evidence=[self.ref('unknown', b'unknown')], usage={'tokens': 20})
        with self.assertRaisesRegex(BudgetError, 'USAGE_CANNOT_DECREASE'):
            self.complete(reconciliation=self.readback())
        self.apply('settle', attempt_id='r1-methods', outcome='COMPLETE', verdict='PASS',
                   evidence=[self.ref('final-result', b'actual returned result')], usage={'tokens': 21},
                   reconciliation=self.readback(verdict='PASS'))
        (self.root / 'final-result').write_bytes(b'changed after acceptance')
        with self.assertRaises(BudgetError):
            self.begin('reuse')
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 1)

    def test_journal_write_failure_rolls_back_round_and_attempts_atomically(self):
        self.register()
        with sqlite3.connect(self.db) as fault:
            fault.execute("CREATE TRIGGER fail_journal BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'simulated journal failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.begin()
        self.ledger.close()
        self.ledger = Ledger(self.db, project_id='new-project')
        self.assertEqual(self.ledger.status('chapter')['rounds'], [])
        with sqlite3.connect(self.db) as fault:
            fault.execute('DROP TRIGGER fail_journal')
        self.begin()
        self.assertEqual(len(self.ledger.status('chapter')['rounds']), 1)

    def test_separate_preregistered_scopes_can_be_pending_in_parallel(self):
        self.register()
        self.register(scope='other-chapter', lineage='other-issues')
        self.begin()
        self.begin('other-r1', scope='other-chapter')
        self.complete('other-r1-methods')
        self.assertEqual(self.ledger.status('other-chapter')['completed_rounds'], 1)
        self.assertEqual(self.ledger.status('chapter')['completed_rounds'], 0)


if __name__ == '__main__':
    unittest.main()
