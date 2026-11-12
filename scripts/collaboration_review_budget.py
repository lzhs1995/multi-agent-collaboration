#!/usr/bin/env python3
"""Explicit new-task review accounting; never sends, authorizes, or grades a review."""
import sys
sys.dont_write_bytecode = True

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time


class BudgetError(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise BudgetError(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def label(value):
    need(isinstance(value, str) and value.strip() == value and 0 < len(value) <= 256
         and not any(ord(c) < 32 for c in value), 'INVALID_LABEL')
    return value


def fields(value, required, optional=()):
    need(isinstance(value, dict) and set(required) <= value.keys()
         and value.keys() <= set(required) | set(optional), 'INVALID_FIELDS')


def reference(value):
    fields(value, ('path', 'bytes', 'sha256'))
    need(isinstance(value['path'], str) and Path(value['path']).is_absolute(), 'ABSOLUTE_REFERENCE_REQUIRED')
    p = Path(value['path'])
    need(type(value['bytes']) is int and 0 <= value['bytes'] <= 512 * 1024**2, 'REFERENCE_SIZE_LIMIT')
    need(isinstance(value['sha256'], str) and len(value['sha256']) == 64
         and all(c in '0123456789abcdef' for c in value['sha256']), 'INVALID_SHA256')
    before = p.stat()
    need(stat.S_ISREG(before.st_mode) and not p.is_symlink(), 'REGULAR_REFERENCE_REQUIRED')
    need(before.st_size == value['bytes'], 'REFERENCE_BYTES_CHANGED')
    digest, size = hashlib.sha256(), 0
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(block)
            need(size <= value['bytes'], 'REFERENCE_CHANGED')
            digest.update(block)
    after = p.stat()
    keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    need(all(getattr(before, k) == getattr(after, k) for k in keys)
         and size == value['bytes'] and digest.hexdigest() == value['sha256'], 'REFERENCE_CHANGED')
    return dict(value)


SCHEMA = '''
CREATE TABLE project (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE scopes (id TEXT PRIMARY KEY, lineage TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
CREATE TABLE aliases (id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES scopes(id), authorization TEXT NOT NULL);
CREATE TABLE rounds (id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES scopes(id),
 ordinal INTEGER NOT NULL, artifact TEXT NOT NULL, intent TEXT NOT NULL, status TEXT NOT NULL,
 UNIQUE(scope,ordinal));
CREATE TABLE attempts (id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES rounds(id),
 role TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL, settlement TEXT,
 usage TEXT NOT NULL DEFAULT '{}');
CREATE TABLE verdicts (review_key TEXT PRIMARY KEY, attempt TEXT UNIQUE NOT NULL REFERENCES attempts(id),
 scope TEXT NOT NULL REFERENCES scopes(id), role TEXT NOT NULL, artifact TEXT NOT NULL,
 standard TEXT NOT NULL, verdict TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE events (operation_id TEXT PRIMARY KEY, action TEXT NOT NULL, request TEXT NOT NULL,
 response TEXT NOT NULL, at_ns INTEGER NOT NULL);
PRAGMA user_version=1;
'''


class Ledger:
    @classmethod
    def create(cls, path, *, project_id, owner, authorization, new_tasks_only=False):
        need(new_tasks_only is True, 'NEW_TASKS_ONLY_REQUIRED')
        label(project_id)
        label(owner)
        authorization = reference(authorization)
        path = Path(path)
        need(path.is_absolute() and path.parent.is_dir(), 'EXPLICIT_EXISTING_PARENT_REQUIRED')
        # Never truncate/reinitialize a ledger or infer a new project after a crash.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
            db.execute('INSERT INTO project VALUES (?,?)', (project_id, encoded(dict(
                owner=owner, authorization=authorization, new_tasks_only=True, max_completed_rounds=3))))
            db.execute('COMMIT')
        finally:
            db.close()
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        return cls(path, project_id=project_id)

    def __init__(self, path, *, project_id):
        path = Path(path)
        need(path.is_absolute() and path.is_file() and not path.is_symlink(), 'EXISTING_LEDGER_REQUIRED')
        self.db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=0.25, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA synchronous=FULL')
            need(self.db.execute('PRAGMA user_version').fetchone()[0] == 1, 'UNKNOWN_LEDGER_VERSION')
            rows = self.db.execute('SELECT * FROM project').fetchall()
            need(len(rows) == 1 and rows[0]['id'] == project_id, 'PROJECT_ID_MISMATCH')
            self.project_id = project_id
            self.project = json.loads(rows[0]['data'])
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as exc:
            raise BudgetError('LEDGER_BUSY_OR_UNAVAILABLE') from exc
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _scope(self, value):
        label(value)
        alias = self.db.execute('SELECT scope FROM aliases WHERE id=?', (value,)).fetchone()
        key = alias['scope'] if alias else value
        row = self.db.execute('SELECT * FROM scopes WHERE id=?', (key,)).fetchone()
        need(row is not None, 'SCOPE_NOT_REGISTERED')
        return row, json.loads(row['data'])

    def _status(self, scope_id):
        scope, contract = self._scope(scope_id)
        rounds = []
        for row in self.db.execute('SELECT * FROM rounds WHERE scope=? ORDER BY ordinal', (scope['id'],)):
            item = dict(row)
            item['artifact'] = json.loads(item['artifact'])
            item['intent'] = json.loads(item['intent'])
            item['attempts'] = []
            for attempt in self.db.execute('SELECT * FROM attempts WHERE round_id=? ORDER BY rowid', (row['id'],)):
                data = dict(attempt)
                for key in ('actor', 'usage', 'settlement'):
                    data[key] = json.loads(data[key]) if data[key] is not None else None
                item['attempts'].append(data)
            rounds.append(item)
        return dict(project_id=self.project_id, scope_id=scope['id'], issue_lineage=scope['lineage'],
                    contract=contract, rounds=rounds, completed_rounds=sum(r['status'] == 'COMPLETE' for r in rounds),
                    max_completed_rounds=3, unresolved=any(r['status'] != 'COMPLETE' for r in rounds),
                    authority_verified=False, domain_acceptance_proven=False, dispatch_authorized=False)

    def status(self, scope_id):
        self.db.execute('BEGIN')
        try:
            return self._status(scope_id)
        finally:
            self.db.execute('ROLLBACK')

    def apply(self, request):
        need(isinstance(request, dict), 'OBJECT_REQUIRED')
        request = json.loads(encoded(request))
        operation_id, action = label(request.get('operation_id')), label(request.get('action'))
        payload = {k: v for k, v in request.items() if k not in ('operation_id', 'action')}
        handler = {'register_scope': self._register, 'begin_round': self._begin, 'settle': self._settle,
                   'mark_unknown': self._unknown, 'retry': self._retry, 'alias_scope': self._alias}.get(action)
        need(handler is not None, 'UNKNOWN_ACTION')
        with self.transaction():
            old = self.db.execute('SELECT * FROM events WHERE operation_id=?', (operation_id,)).fetchone()
            if old:
                need(old['request'] == encoded(request), 'OPERATION_ID_CONFLICT')
                return dict(json.loads(old['response']), idempotent=True)
            result = handler(payload)
            result.update(authority_verified=False, domain_acceptance_proven=False, dispatch_authorized=False)
            self.db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                            (operation_id, action, encoded(request), encoded(result), time.time_ns()))
            return result

    def _decision(self, ref, action, binding, owner):
        reference(ref)
        need(ref['bytes'] <= 1024**2, 'OWNER_DECISION_TOO_LARGE')
        raw = Path(ref['path']).read_bytes()
        need(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'OWNER_DECISION_CHANGED')
        try:
            decision = json.loads(raw, object_pairs_hook=strict_object)
        except (ValueError, UnicodeError) as exc:
            raise BudgetError('OWNER_DECISION_JSON_REQUIRED') from exc
        expected = dict(schema='review-owner-decision-v1', project_id=self.project_id,
                        owner=owner, action=action, binding=binding)
        need(decision == expected, 'OWNER_DECISION_BINDING_MISMATCH')

    def _readback(self, ref, attempt, outcome, verdict):
        reference(ref)
        need(ref['bytes'] <= 1024**2, 'READBACK_TOO_LARGE')
        raw = Path(ref['path']).read_bytes()
        need(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'READBACK_CHANGED')
        try:
            observed = json.loads(raw, object_pairs_hook=strict_object)
        except (ValueError, UnicodeError) as exc:
            raise BudgetError('READBACK_JSON_REQUIRED') from exc
        expected = dict(schema='review-attempt-readback-v1', project_id=self.project_id,
                        round_id=attempt['round_id'], attempt_id=attempt['id'],
                        outcome=outcome, verdict=verdict)
        need(observed == expected, 'READBACK_BINDING_MISMATCH')

    def _register(self, data):
        fields(data, ('scope_id', 'issue_lineage', 'owner', 'authorization', 'standard',
                      'required_reviews', 'max_attempts_per_review'))
        for key in ('scope_id', 'issue_lineage', 'owner'):
            label(data[key])
        need(data['owner'] == self.project['owner'], 'ORIGINAL_OWNER_DECISION_REQUIRED')
        self._decision(data['authorization'], 'register_scope',
                       {k: v for k, v in data.items() if k != 'authorization'}, data['owner'])
        reference(data['standard'])
        roles = data['required_reviews']
        need(isinstance(roles, list) and 1 <= len(roles) <= 32, 'REQUIRED_REVIEWS_REQUIRED')
        for role in roles:
            fields(role, ('reviewer_role', 'review_scope'))
            label(role['reviewer_role'])
            label(role['review_scope'])
        need(len({r['reviewer_role'] for r in roles}) == len(roles), 'DUPLICATE_REVIEW_ROLE')
        need(type(data['max_attempts_per_review']) is int and 1 <= data['max_attempts_per_review'] <= 3,
             'FINITE_ATTEMPT_LIMIT_REQUIRED')
        old = self.db.execute('SELECT data FROM scopes WHERE id=?', (data['scope_id'],)).fetchone()
        if old:
            need(old['data'] == encoded(data), 'SCOPE_REGISTRATION_FROZEN')
            return dict(status='REGISTERED', scope_id=data['scope_id'], reused=True)
        need(not self.db.execute('SELECT 1 FROM scopes WHERE lineage=?', (data['issue_lineage'],)).fetchone(),
             'LINEAGE_ALREADY_REGISTERED_USE_ALIAS')
        need(not self.db.execute('SELECT 1 FROM aliases WHERE id=?', (data['scope_id'],)).fetchone(), 'SCOPE_NAME_TAKEN')
        self.db.execute('INSERT INTO scopes VALUES (?,?,?)', (data['scope_id'], data['issue_lineage'], encoded(data)))
        return dict(status='REGISTERED', scope_id=data['scope_id'])

    def _alias(self, data):
        fields(data, ('scope_id', 'issue_lineage', 'alias', 'authorized_by', 'authorization'))
        scope, contract = self._scope(data['scope_id'])
        label(data['alias'])
        need(data['issue_lineage'] == scope['lineage'], 'LINEAGE_IS_FROZEN')
        need(data['authorized_by'] == contract['owner'], 'ORIGINAL_OWNER_DECISION_REQUIRED')
        binding = dict(scope_id=scope['id'], issue_lineage=scope['lineage'], alias=data['alias'])
        self._decision(data['authorization'], 'alias_scope', binding, contract['owner'])
        need(not self.db.execute('SELECT 1 FROM scopes WHERE id=?', (data['alias'],)).fetchone(), 'SCOPE_NAME_TAKEN')
        old = self.db.execute('SELECT * FROM aliases WHERE id=?', (data['alias'],)).fetchone()
        if old:
            need(old['scope'] == scope['id'] and old['authorization'] == encoded(data['authorization']),
                 'ALIAS_IS_IMMUTABLE')
        else:
            self.db.execute('INSERT INTO aliases VALUES (?,?,?)',
                            (data['alias'], scope['id'], encoded(data['authorization'])))
        return dict(status='ALIAS_REUSES_CANONICAL_SCOPE', scope_id=scope['id'], alias=data['alias'])

    @staticmethod
    def _review_key(scope, artifact, role, standard):
        return hashlib.sha256(encoded([scope, artifact['sha256'], artifact['bytes'], role,
                                       standard['sha256'], standard['bytes']]).encode()).hexdigest()

    def _begin(self, data):
        fields(data, ('scope_id', 'round_id', 'artifact', 'attempts'))
        scope, contract = self._scope(data['scope_id'])
        label(data['round_id'])
        reference(data['artifact'])
        reference(contract['standard'])
        attempts = data['attempts']
        need(isinstance(attempts, list) and attempts, 'ATTEMPTS_REQUIRED')
        for attempt in attempts:
            fields(attempt, ('attempt_id', 'reviewer_role', 'actor'))
            label(attempt['attempt_id'])
            label(attempt['reviewer_role'])
            fields(attempt['actor'], ('owner', 'model', 'route'))
            for value in attempt['actor'].values():
                label(value)
        need(len({a['attempt_id'] for a in attempts}) == len(attempts), 'DUPLICATE_ATTEMPT_ID')
        expected = {r['reviewer_role'] for r in contract['required_reviews']}
        need(len(attempts) == len(expected) and {a['reviewer_role'] for a in attempts} == expected,
             'FROZEN_REVIEW_SCOPES_REQUIRED')
        old = self.db.execute('SELECT intent FROM rounds WHERE id=?', (data['round_id'],)).fetchone()
        if old:
            need(old['intent'] == encoded(data), 'ROUND_ID_CONFLICT')
            return dict(status='EXISTING_INTENT', original_round_id=data['round_id'], reused=True)
        current = self._status(scope['id'])
        need(not current['unresolved'], 'ORIGINAL_ATTEMPT_UNRESOLVED')
        reused = []
        for attempt in attempts:
            key = self._review_key(scope['id'], data['artifact'], attempt['reviewer_role'], contract['standard'])
            row = self.db.execute('SELECT * FROM verdicts WHERE review_key=?', (key,)).fetchone()
            if row:
                self._evidence(json.loads(row['evidence']))
                reused.append(dict(row))
        if reused:
            need(len(reused) == len(attempts), 'PARTIAL_VERDICTS_RECONCILE_ORIGINAL_ROUND')
            return dict(status='REUSE', verdicts=reused, new_round=False)
        need(current['completed_rounds'] < 3, 'REVIEW_BUDGET_EXHAUSTED')
        for attempt in attempts:
            need(not self.db.execute('SELECT 1 FROM attempts WHERE id=?', (attempt['attempt_id'],)).fetchone(),
                 'ATTEMPT_ID_ALREADY_USED')
        self.db.execute('INSERT INTO rounds VALUES (?,?,?,?,?,?)',
                        (data['round_id'], scope['id'], current['completed_rounds'] + 1,
                         encoded(data['artifact']), encoded(data), 'OPEN'))
        for attempt in attempts:
            self.db.execute('INSERT INTO attempts(id,round_id,role,actor,status) VALUES (?,?,?,?,?)',
                            (attempt['attempt_id'], data['round_id'], attempt['reviewer_role'],
                             encoded(attempt['actor']), 'PENDING'))
        return dict(status='INTENT_RECORDED', round_id=data['round_id'], new_round=True)

    @staticmethod
    def _evidence(evidence):
        need(isinstance(evidence, list) and 1 <= len(evidence) <= 32, 'EVIDENCE_REQUIRED')
        for ref in evidence:
            reference(ref)

    def _attempt(self, attempt_id):
        label(attempt_id)
        attempt = self.db.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone()
        need(attempt is not None, 'UNKNOWN_ATTEMPT')
        return attempt

    @staticmethod
    def _usage(value, previous):
        need(isinstance(value, dict) and all(isinstance(k, str) and k and type(v) is int and v >= 0
                                           for k, v in value.items()), 'INVALID_USAGE')
        old = json.loads(previous)
        need(all(value.get(k, v) >= v for k, v in old.items()), 'USAGE_CANNOT_DECREASE')
        return dict(old, **value)

    def _unknown(self, data):
        fields(data, ('attempt_id', 'reason', 'evidence', 'usage'))
        label(data['reason'])
        self._evidence(data['evidence'])
        attempt = self._attempt(data['attempt_id'])
        need(attempt['status'] in ('PENDING', 'UNKNOWN'), 'ORIGINAL_RESULT_IMMUTABLE')
        usage = self._usage(data['usage'], attempt['usage'])
        self.db.execute("UPDATE attempts SET status='UNKNOWN',usage=? WHERE id=?", (encoded(usage), attempt['id']))
        return dict(status='UNKNOWN_REQUIRES_ORIGINAL_READBACK', attempt_id=attempt['id'])

    def _settle(self, data):
        fields(data, ('attempt_id', 'outcome', 'verdict', 'evidence', 'usage'), ('reconciliation',))
        complete = data['outcome'] == 'COMPLETE'
        need((complete and data['verdict'] in ('PASS', 'NEEDS_CHANGES', 'FAIL'))
             or (data['outcome'] == 'INCOMPLETE' and data['verdict'] is None), 'INVALID_OUTCOME')
        self._evidence(data['evidence'])
        attempt = self._attempt(data['attempt_id'])
        if attempt['settlement'] is not None:
            need(attempt['settlement'] == encoded(data), 'ORIGINAL_RESULT_IMMUTABLE')
            return dict(status='RESULT_REUSED', attempt_id=data['attempt_id'], newly_recorded=False)
        if attempt['status'] == 'UNKNOWN':
            need('reconciliation' in data, 'ORIGINAL_READBACK_REQUIRED')
        if 'reconciliation' in data:
            self._readback(data['reconciliation'], attempt, data['outcome'], data['verdict'])
        usage = self._usage(data['usage'], attempt['usage'])
        round_ = self.db.execute('SELECT * FROM rounds WHERE id=?', (attempt['round_id'],)).fetchone()
        scope, contract = self._scope(round_['scope'])
        artifact = json.loads(round_['artifact'])
        key = self._review_key(scope['id'], artifact, attempt['role'], contract['standard'])
        if complete:
            reference(artifact)
            reference(contract['standard'])
            self.db.execute('INSERT INTO verdicts VALUES (?,?,?,?,?,?,?,?)',
                            (key, attempt['id'], scope['id'], attempt['role'], encoded(artifact),
                             encoded(contract['standard']), data['verdict'], encoded(data['evidence'])))
        self.db.execute('UPDATE attempts SET status=?,settlement=?,usage=? WHERE id=?',
                        ('COMPLETE' if complete else 'INCOMPLETE', encoded(data), encoded(usage), attempt['id']))
        completed_roles = {r[0] for r in self.db.execute(
            "SELECT role FROM attempts WHERE round_id=? AND status='COMPLETE'", (round_['id'],))}
        finished = completed_roles == {r['reviewer_role'] for r in contract['required_reviews']}
        if finished:
            self.db.execute("UPDATE rounds SET status='COMPLETE' WHERE id=?", (round_['id'],))
        return dict(status='RESULT_RECORDED', attempt_id=attempt['id'], newly_recorded=True,
                    completed_round=finished)

    def _retry(self, data):
        fields(data, ('attempt_id', 'new_attempt_id', 'actor', 'authorization', 'authorized_by'))
        previous = self._attempt(data['attempt_id'])
        need(previous['status'] == 'INCOMPLETE', 'ORIGINAL_INCOMPLETE_RESULT_REQUIRED')
        round_ = self.db.execute('SELECT * FROM rounds WHERE id=?', (previous['round_id'],)).fetchone()
        scope, contract = self._scope(round_['scope'])
        need(data['authorized_by'] == contract['owner'], 'ORIGINAL_OWNER_DECISION_REQUIRED')
        binding = dict(scope_id=scope['id'], issue_lineage=scope['lineage'],
                       attempt_id=data['attempt_id'], new_attempt_id=data['new_attempt_id'])
        self._decision(data['authorization'], 'retry', binding, contract['owner'])
        label(data['new_attempt_id'])
        fields(data['actor'], ('owner', 'model', 'route'))
        for value in data['actor'].values():
            label(value)
        history = self.db.execute('SELECT * FROM attempts WHERE round_id=? AND role=?',
                                  (round_['id'], previous['role'])).fetchall()
        need(all(a['status'] == 'INCOMPLETE' for a in history), 'ORIGINAL_ATTEMPT_UNRESOLVED_OR_COMPLETE')
        need(len(history) < contract['max_attempts_per_review'], 'ATTEMPT_BUDGET_EXHAUSTED')
        need(not self.db.execute('SELECT 1 FROM attempts WHERE id=?', (data['new_attempt_id'],)).fetchone(),
             'ATTEMPT_ID_ALREADY_USED')
        self.db.execute('INSERT INTO attempts(id,round_id,role,actor,status) VALUES (?,?,?,?,?)',
                        (data['new_attempt_id'], round_['id'], previous['role'], encoded(data['actor']), 'PENDING'))
        return dict(status='RETRY_INTENT_RECORDED', round_id=round_['id'], attempt_id=data['new_attempt_id'],
                    previous_attempt=data['attempt_id'], new_round=False)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def read_request(path):
    p = Path(path)
    need(p.is_absolute(), 'ABSOLUTE_REQUEST_REQUIRED')
    with p.open('rb') as stream:
        raw = stream.read(1024**2 + 1)
    need(len(raw) <= 1024**2, 'REQUEST_TOO_LARGE')
    return json.loads(raw, object_pairs_hook=strict_object)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--project-id', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    for action in ('create', 'apply'):
        sub.add_parser(action).add_argument('--request', required=True)
    sub.add_parser('status').add_argument('--scope-id', required=True)
    args = parser.parse_args(argv)
    ledger = None
    try:
        if args.command == 'create':
            data = read_request(args.request)
            fields(data, ('owner', 'authorization', 'new_tasks_only'))
            ledger = Ledger.create(args.db, project_id=args.project_id, **data)
            result = dict(status='PROJECT_CREATED', project_id=args.project_id,
                          authority_verified=False, dispatch_authorized=False, domain_acceptance_proven=False)
        else:
            ledger = Ledger(args.db, project_id=args.project_id)
            result = ledger.status(args.scope_id) if args.command == 'status' else ledger.apply(read_request(args.request))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BudgetError, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(json.dumps(dict(status='ERROR', error=str(exc), dispatch_authorized=False)), file=sys.stderr)
        return 2
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == '__main__':
    raise SystemExit(main())
