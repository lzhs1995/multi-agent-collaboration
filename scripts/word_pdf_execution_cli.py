#!/usr/bin/env python3
"""One explicit step of a newly bound Word PDF operation; never configure or retry."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from resource_broker import Broker


def read_json(path, digest):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path or path.is_symlink():
        raise ValueError('ABSOLUTE_CANONICAL_JSON_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 8 * 1024**2:
            raise ValueError('BOUNDED_REGULAR_JSON_REQUIRED')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            body = stream.read(8 * 1024**2 + 1)
        facts = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if facts(before) != facts(os.fstat(fd)) or facts(before) != facts(path.stat()):
            raise ValueError('JSON_CHANGED_DURING_READ')
        if len(body) != before.st_size or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError('JSON_SHA256_MISMATCH')
    finally:
        os.close(fd)
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError('NONFINITE_JSON')
    value = json.loads(body, object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError('JSON_OBJECT_REQUIRED')
    return value


def existing_root(value):
    if not value:
        raise ValueError('EXPLICIT_EXISTING_RESOURCE_ROOT_REQUIRED')
    root = Path(value)
    if (not root.is_absolute() or root.resolve(strict=True) != root
            or root.is_symlink() or not root.is_dir()):
        raise ValueError('EXPLICIT_EXISTING_RESOURCE_ROOT_REQUIRED')
    database = root / 'queue.sqlite3'
    if database.is_symlink() or not stat.S_ISREG(database.lstat().st_mode):
        raise ValueError('EXISTING_RESOURCE_DATABASE_REQUIRED')
    return root


def execute(args):
    contract = read_json(args.contract, args.contract_sha256)
    # This must precede Broker construction: failed native admission touches no DB.
    Broker.validate_word_pdf_contract(contract)
    if args.command == 'preflight':
        return {'status': 'WORD_PDF_PREFLIGHT_PASSED', 'application_started': False,
                'resource_database_opened': False}
    root = existing_root(args.root)
    lease = None
    proof = None
    if args.command in ('run', 'release'):
        if not args.lease or not args.lease_sha256:
            raise ValueError('PINNED_ORIGINAL_LEASE_REQUIRED')
        lease = read_json(args.lease, args.lease_sha256)
        if args.command == 'release':
            if not args.proof or not args.proof_sha256:
                raise ValueError('PINNED_ORIGINAL_RELEASE_PROOF_REQUIRED')
            proof = read_json(args.proof, args.proof_sha256)
    if args.command == 'grant' and not args.ticket:
        raise ValueError('ORIGINAL_TICKET_REQUIRED')
    broker = Broker(root)
    try:
        if args.command == 'request':
            return broker.request_enforced(contract)
        ticket = args.ticket if args.command == 'grant' else lease['id']
        execution = broker._execution()
        binding, stored = execution.binding(ticket)
        if stored != contract:
            raise ValueError('ORIGINAL_TICKET_CONTRACT_MISMATCH')
        if args.command == 'grant':
            return broker.grant_enforced(ticket, ttl=args.ttl)
        bound = lease['execution']
        if (bound.get('contract_sha256') != binding['contract_sha256']
                or bound.get('generation') != binding['generation']
                or any(bound.get(key) != contract[key] for key in (
                    'operation', 'request_revision', 'attempt_id'))):
            raise ValueError('ORIGINAL_LEASE_EXECUTION_MISMATCH')
        if args.command == 'run':
            return broker.run_word_pdf_enforced(ticket, lease['token'], **{
                key: bound[key] for key in ('generation', 'operation', 'request_revision', 'attempt_id')})
        return broker.release_word_pdf_enforced(
            ticket, lease['token'], proof, generation=bound['generation'], reconcile=args.reconcile)
    finally:
        broker.db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preflight', 'request', 'grant', 'run', 'release'))
    parser.add_argument('--contract', required=True)
    parser.add_argument('--contract-sha256', required=True)
    parser.add_argument('--root', help='Original explicitly configured resource root; no default or configure step')
    parser.add_argument('--ticket')
    parser.add_argument('--ttl', type=int, default=600)
    parser.add_argument('--lease')
    parser.add_argument('--lease-sha256')
    parser.add_argument('--proof')
    parser.add_argument('--proof-sha256')
    parser.add_argument('--reconcile', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'REFUSED_OR_INCOMPLETE', 'error_type': type(exc).__name__,
                          'error': str(exc), 'automatic_retry': False,
                          'original_attempt_readback_required': args.command in ('run', 'release')},
                         ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
