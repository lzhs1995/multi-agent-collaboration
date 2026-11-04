"""CLI admission boundaries; no application or shared resource database access."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import word_pdf_execution_cli as cli


class WordCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.contract = {'schema': 'resource-execution-word-pdf-v1',
                         'operation': 'owned_word_pdf_export', 'request_revision': 'a' * 64,
                         'attempt_id': 'original-attempt'}
        self.path, self.digest = self.write('contract.json', self.contract)

    def write(self, name, value):
        path = self.root / name
        body = json.dumps(value).encode()
        path.write_bytes(body)
        return str(path), hashlib.sha256(body).hexdigest()

    def args(self, command, **changes):
        values = dict(command=command, contract=self.path, contract_sha256=self.digest,
                      root=str(self.root), ticket='ticket-1', ttl=600, lease=None,
                      lease_sha256=None, proof=None, proof_sha256=None, reconcile=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_native_refusal_never_constructs_broker_or_creates_database(self):
        with patch.object(cli, 'Broker') as broker:
            broker.validate_word_pdf_contract.side_effect = ValueError('native identity unknown')
            with self.assertRaisesRegex(ValueError, 'native identity unknown'):
                cli.execute(self.args('request'))
            broker.assert_not_called()
        self.assertFalse((self.root / 'queue.sqlite3').exists())

    def test_preflight_never_opens_resource_root(self):
        with patch.object(cli, 'Broker') as broker, patch.object(cli, 'existing_root') as root:
            result = cli.execute(self.args('preflight', root=None))
            broker.validate_word_pdf_contract.assert_called_once_with(self.contract)
            broker.assert_not_called()
            root.assert_not_called()
        self.assertFalse(result['application_started'])
        self.assertFalse(result['resource_database_opened'])

    def test_contract_digest_and_duplicate_keys_refuse_before_native(self):
        with patch.object(cli, 'Broker') as broker:
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                cli.execute(self.args('request', contract_sha256='0' * 64))
            broker.validate_word_pdf_contract.assert_not_called()
            raw = b'{"schema":"a","schema":"b"}'
            Path(self.path).write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'DUPLICATE_JSON_KEY'):
                cli.read_json(self.path, hashlib.sha256(raw).hexdigest())

    def test_missing_root_database_does_not_create_one(self):
        with patch.object(cli, 'Broker') as broker:
            with self.assertRaises(FileNotFoundError):
                cli.execute(self.args('request'))
            broker.assert_not_called()
        self.assertFalse((self.root / 'queue.sqlite3').exists())

    def prepare_lease(self):
        (self.root / 'queue.sqlite3').write_bytes(b'fixture not opened by mock')
        bound = dict(generation=7, contract_sha256='b' * 64,
                     **{key: self.contract[key] for key in ('operation', 'request_revision', 'attempt_id')})
        path, digest = self.write('lease.json', {'id': 'ticket-1', 'token': 'original-token', 'execution': bound})
        return bound, path, digest

    def test_run_uses_exact_lease_once_and_never_releases_automatically(self):
        bound, path, digest = self.prepare_lease()
        with patch.object(cli, 'Broker') as factory:
            broker = factory.return_value
            broker._execution.return_value.binding.return_value = (
                dict(generation=7, contract_sha256='b' * 64), self.contract)
            broker.run_word_pdf_enforced.return_value = {'state': 'TERMINAL'}
            cli.execute(self.args('run', lease=path, lease_sha256=digest))
            broker.run_word_pdf_enforced.assert_called_once_with(
                'ticket-1', 'original-token', **{k: bound[k] for k in (
                    'generation', 'operation', 'request_revision', 'attempt_id')})
            broker.release_word_pdf_enforced.assert_not_called()
            broker.grant_enforced.assert_not_called()
            broker.db.close.assert_called_once()

    def test_stale_generation_or_contract_refuses_before_driver(self):
        _, path, digest = self.prepare_lease()
        with patch.object(cli, 'Broker') as factory:
            broker = factory.return_value
            broker._execution.return_value.binding.return_value = (
                dict(generation=8, contract_sha256='b' * 64), self.contract)
            with self.assertRaisesRegex(ValueError, 'LEASE_EXECUTION_MISMATCH'):
                cli.execute(self.args('run', lease=path, lease_sha256=digest))
            broker.run_word_pdf_enforced.assert_not_called()
            broker.db.close.assert_called_once()

    def test_failure_returns_nonzero_without_retry(self):
        with patch.object(cli, 'execute', side_effect=RuntimeError('unknown original attempt')) as action:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = cli.main(['run', '--contract', self.path, '--contract-sha256', self.digest])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(output.getvalue())['automatic_retry'])
            action.assert_called_once()


if __name__ == '__main__':
    unittest.main()
