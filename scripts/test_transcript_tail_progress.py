"""Oversized/incomplete JSONL safety and reply recovery; all IO is isolated."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cmux_transcript_tail import TranscriptTail
import executor_ready as ready
import executor_reask as reask
import executor_reply as replies


class TailTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'transcript.jsonl'
        self.path.write_bytes(b'')

    def tail(self, position=None):
        return TranscriptTail(self.path, position, read_budget=16, record_limit=256)

    def append(self, raw):
        with self.path.open('ab') as stream:
            stream.write(raw)

    def test_oversized_record_preserves_boundary_then_returns_whole_original(self):
        tail = self.tail()
        raw = json.dumps(dict(type='user', message=dict(content='x' * 65))).encode() + b'\n'
        self.append(raw)
        for _ in range(len(raw) // 16):
            self.assertEqual(tail.lines(), [])
            self.assertEqual(tail.offset, 0)
            self.assertFalse(tail.observation['proves_receipt'])
            tail = self.tail(tail.position())
        self.assertEqual(tail.lines(), [raw[:-1]])
        self.assertEqual(tail.offset, len(raw))
        self.assertNotIn('pending', tail.position())
        self.assertEqual(tail.observation['record_sha256'], hashlib.sha256(raw).hexdigest())

    def test_partial_record_and_bytewise_appends_do_not_inflate_ledger(self):
        tail = self.tail()
        for byte in b'x' * 75:
            self.append(bytes([byte]))
            self.assertEqual(tail.lines(), [])
            self.assertEqual(tail.offset, 0)
            tail = self.tail(tail.position())
            if tail.pending:
                self.assertLessEqual(len(tail.pending['chunks']), 75 // 16)
        self.append(b'\n')
        self.assertEqual(tail.lines(), [b'x' * 75])

    def test_same_inode_changed_partial_content_never_completes(self):
        tail = self.tail()
        self.append(b'a' * 32)
        tail.lines()
        position = tail.position()
        with self.path.open('r+b') as stream:
            stream.write(b'b')
        self.append(b'\n')
        tail = self.tail(position)
        tail.lines()
        with self.assertRaisesRegex(ValueError, 'PENDING_CONTENT_CHANGED'):
            tail.lines()
        self.assertEqual(tail.offset, 0)
        self.assertIsNotNone(tail.pending)

    def test_replacement_and_truncation_preserve_original_position(self):
        tail = self.tail()
        self.append(b'a' * 20)
        tail.lines()
        position = tail.position()
        replacement = self.path.with_name('replacement')
        replacement.write_bytes(b'z' * 32)
        replacement.replace(self.path)
        with self.assertRaisesRegex(ValueError, 'TRANSCRIPT_CHANGED'):
            self.tail(position)
        tail = self.tail()
        tail.offset = 0
        tail.lines()
        position = tail.position()
        self.path.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'PENDING_INVALID'):
            self.tail(position)

    def test_hard_limit_blocks_unknown_record_without_skipping(self):
        tail = self.tail()
        self.append(b'?' * 270 + b'\n')
        for _ in range(16):
            self.assertEqual(tail.lines(), [])
        with self.assertRaisesRegex(ValueError, 'EXCEEDS_RECORD_LIMIT'):
            tail.lines()
        self.assertEqual(tail.offset, 0)
        self.assertEqual(tail.observation['status'], 'RECORD_LIMIT_REACHED')

    def test_normal_complete_records_stay_under_poll_budget(self):
        tail = self.tail()
        self.append(b'first\nsecond\nthird\n')
        self.assertEqual(tail.lines(), [b'first', b'second'])
        self.assertLessEqual(tail.offset, 16)
        self.assertEqual(tail.lines(), [b'third'])

    def test_exact_native_record_pins_preserve_crlf_bytes_and_offsets(self):
        tail = self.tail()
        raw = b'first\r\nsecond\n'
        self.append(raw)
        self.assertEqual(tail.lines(), [b'first\r', b'second'])
        self.assertEqual([p['offset'] for p in tail.record_observations], [0, 7])
        self.assertEqual([p['bytes'] for p in tail.record_observations], [7, 7])
        self.assertEqual(tail.record_observations[0]['sha256'], hashlib.sha256(b'first\r\n').hexdigest())


class ReaskTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / 'transcript.jsonl'
        self.path.write_bytes(b'')
        self.caller, self.supervisor = 'caller', 'supervisor'
        mailbox = self.root / 'mailbox'
        mailbox.mkdir()
        self.state = dict(caller_surface_uuid=self.caller, supervisor=self.supervisor,
            task_id='task', episode_id='episode', started_epoch=0, asks=[dict(marker='EXECUTOR_READY_1')],
            mailbox=str(mailbox), channels=[], transcript=str(self.path),
            tail_position=ready.Tail(self.path).position())
        self.body = dict(marker='EXECUTOR_READY_1', caller_surface_uuid=self.caller,
            supervisor_uuid=self.supervisor, task_id='task', episode_id='episode', status='ACK')

    def image_and_reply(self):
        image = dict(type='user', message=dict(content=[dict(type='tool_result', content=[
            dict(type='image', source=dict(type='base64', data='A' * (ready.MAX_TRANSCRIPT_BYTES * 5)))])]))
        raw = json.dumps(image).encode() + b'\n'
        reply = json.dumps(dict(type='user',message=dict(role='user',content=json.dumps(self.body)))).encode()+b'\n'
        self.path.write_bytes(raw + reply)
        return raw

    def test_image_record_cannot_forge_reply_and_later_native_reply_is_found(self):
        image = self.image_and_reply()
        for _ in range(5):
            self.assertIsNone(reask.find_reply(self.state))
            self.assertEqual(self.state['tail_position']['offset'], 0)
            self.assertFalse(self.state['transcript_observation']['proves_receipt'])
            self.state = json.loads(json.dumps(self.state))
        result = reask.find_reply(self.state)
        self.assertEqual(result['source'], 'transcript')
        self.assertEqual(result['body'], self.body)
        self.assertNotIn('pending', self.state['tail_position'])
        raw = self.path.read_bytes()[len(image):]
        self.assertEqual(result['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['raw_bytes'], len(raw))
        self.assertEqual(result['transcript_record']['offset'], len(image))
        self.assertEqual(result['transcript_record']['sha256'], result['raw_sha256'])

    def test_mailbox_stays_available_when_original_record_exceeds_hard_limit(self):
        self.path.write_bytes(b'?' * 130 + b'\n')
        with patch.object(ready,'MAX_TRANSCRIPT_BYTES',16), patch.object(ready,'MAX_TRANSCRIPT_RECORD_BYTES',128):
            for _ in range(9):
                self.assertIsNone(reask.find_reply(self.state))
            self.assertIn('EXCEEDS_RECORD_LIMIT', self.state['transcript_error'])
            original = json.loads(json.dumps(self.state['tail_position']))
            target = Path(self.state['mailbox']) / (self.body['marker'] + '.json')
            target.write_text(json.dumps(self.body))
            result = reask.find_reply(self.state)
        self.assertEqual(result['source'], 'mailbox')
        self.assertEqual(self.state['tail_position'], original)


if __name__ == '__main__':
    unittest.main()
