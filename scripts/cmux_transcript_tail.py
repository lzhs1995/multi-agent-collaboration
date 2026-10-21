"""Bounded JSONL progress without treating a partial record as a native receipt."""
import copy
import hashlib
import os
from pathlib import Path
import stat


class TranscriptTail:
    """Commit only complete records; persist bounded scan progress separately.

    Partial-record bytes remain in the original transcript. The small pending
    ledger binds each scanned range by hash, then verifies all ranges before
    returning a completed record. It neither skips an unknown oversized record
    nor grants receipt or reply authority to a scan observation.
    """

    def __init__(self, path, position=None, *, read_budget=256 * 1024,
                 record_limit=8 * 1024 * 1024):
        if type(read_budget) is not int or type(record_limit) is not int or not 0 < read_budget <= record_limit:
            raise ValueError('REPLY_TRANSCRIPT_BUDGET_INVALID')
        self.path = Path(path) if path else None
        st = self.path.stat() if self.path else None
        if st and not stat.S_ISREG(st.st_mode):
            raise ValueError('REPLY_TRANSCRIPT_NOT_REGULAR')
        self.identity = [st.st_dev, st.st_ino] if st else None
        self.offset = st.st_size if st else 0
        self.read_budget, self.record_limit = read_budget, record_limit
        self.pending = None
        self.observation = None
        self.record_observations = []
        if position is not None:
            if (not isinstance(position, dict) or position.get('identity') != self.identity
                    or type(position.get('offset')) is not int
                    or not 0 <= position['offset'] <= self.offset):
                raise ValueError('REPLY_TRANSCRIPT_CHANGED: preserve idle episode')
            size, self.offset = self.offset, position['offset']
            pending = position.get('pending')
            if pending is not None:
                self._validate_pending(pending, size)
                self.pending = copy.deepcopy(pending)

    def _validate_pending(self, pending, size):
        if (not isinstance(pending, dict) or pending.get('version') != 1
                or pending.get('start') != self.offset
                or type(pending.get('scanned')) is not int
                or not 0 < pending['scanned'] <= self.record_limit
                or self.offset + pending['scanned'] > size
                or not isinstance(pending.get('chunks'), list)
                or not 0 < len(pending['chunks']) <= self.record_limit // self.read_budget + 1):
            raise ValueError('REPLY_TRANSCRIPT_PENDING_INVALID')
        total = 0
        for chunk in pending['chunks']:
            if (not isinstance(chunk, dict) or type(chunk.get('bytes')) is not int
                    or not 0 < chunk['bytes'] <= self.read_budget
                    or not isinstance(chunk.get('sha256'), str)
                    or len(chunk['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in chunk['sha256'])):
                raise ValueError('REPLY_TRANSCRIPT_PENDING_INVALID')
            total += chunk['bytes']
        if total != pending['scanned']:
            raise ValueError('REPLY_TRANSCRIPT_PENDING_INVALID')

    def position(self):
        result = dict(identity=self.identity, offset=self.offset)
        if self.pending is not None:
            result['pending'] = copy.deepcopy(self.pending)
        return result

    def _checked(self, handle, minimum):
        held, current = os.fstat(handle.fileno()), self.path.lstat()
        if (not stat.S_ISREG(held.st_mode) or not stat.S_ISREG(current.st_mode)
                or [held.st_dev, held.st_ino] != self.identity
                or [current.st_dev, current.st_ino] != self.identity
                or min(held.st_size, current.st_size) < minimum):
            raise ValueError('REPLY_TRANSCRIPT_CHANGED')

    def _progress(self, status, **details):
        scanned = self.pending['scanned'] if self.pending else 0
        self.observation = dict(status=status, identity=self.identity,
            committed_offset=self.offset, scan_offset=self.offset + scanned,
            scanned_record_bytes=scanned, read_budget_bytes=self.read_budget,
            record_limit_bytes=self.record_limit, proves_receipt=False, **details)

    def _records(self, raw, start):
        records, observations = [], []
        for part in raw.split(b'\n')[:-1]:
            exact = part + b'\n'
            records.append(part)
            observations.append(dict(offset=start, bytes=len(exact),
                sha256=hashlib.sha256(exact).hexdigest(), identity=self.identity))
            start += len(exact)
        self.record_observations = observations
        return records

    def lines(self):
        self.record_observations = []
        if not self.path:
            return []
        scanned = self.pending['scanned'] if self.pending else 0
        if scanned == self.record_limit:
            self._progress('RECORD_LIMIT_REACHED')
            raise ValueError('REPLY_RECORD_EXCEEDS_RECORD_LIMIT: original record preserved')
        fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as handle:
            self._checked(handle, self.offset + scanned)
            handle.seek(self.offset + scanned)
            wanted = min(self.read_budget, self.record_limit - scanned)
            data = handle.read(wanted)
            self._checked(handle, self.offset + scanned + len(data))
            if not data:
                self._progress('INCOMPLETE_RECORD' if scanned else 'AT_EOF')
                return []
            if self.pending is None:
                boundary = data.rfind(b'\n') + 1
                if boundary:
                    result = self._records(data[:boundary], self.offset)
                    self.offset += boundary
                    self._progress('COMPLETE_RECORDS', read_bytes=len(data))
                    return result
            elif b'\n' in data:
                # The completed record may exceed the polling budget, but this
                # one final parse read is explicitly bounded by record_limit.
                boundary = data.index(b'\n') + 1
                length = scanned + boundary
                handle.seek(self.offset)
                raw = handle.read(length)
                self._checked(handle, self.offset + length)
                cursor = 0
                for chunk in self.pending['chunks']:
                    end = cursor + chunk['bytes']
                    if hashlib.sha256(raw[cursor:end]).hexdigest() != chunk['sha256']:
                        self._progress('PENDING_CONTENT_CHANGED')
                        raise ValueError('REPLY_TRANSCRIPT_PENDING_CONTENT_CHANGED')
                    cursor = end
                if len(raw) != length or raw[scanned:] != data[:boundary]:
                    self._progress('PENDING_CONTENT_CHANGED')
                    raise ValueError('REPLY_TRANSCRIPT_PENDING_CONTENT_CHANGED')
                start = self.offset
                rest = data[boundary:]
                complete = rest.rfind(b'\n') + 1
                result = self._records(raw + rest[:complete], start)
                self.offset += length + complete
                self.pending = None
                self._progress('RECOVERED_COMPLETE_RECORD', record_start=start,
                    record_bytes=length, record_sha256=hashlib.sha256(raw).hexdigest(),
                    parse_read_bytes=length, read_bytes=len(data))
                return result
            if len(data) < wanted:
                # Reread this short suffix on the next poll. Store full chunks
                # only, keeping ledger size bounded even for bytewise appends.
                self._progress('INCOMPLETE_RECORD', read_bytes=len(data),
                               available_record_bytes=scanned + len(data))
                return []
            if self.pending is None:
                self.pending = dict(version=1, start=self.offset, scanned=0, chunks=[])
            self.pending['chunks'].append(dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
            self.pending['scanned'] += len(data)
            self._progress('OVERSIZED_RECORD_IN_PROGRESS' if self.pending['scanned'] >= self.read_budget
                           else 'INCOMPLETE_RECORD', read_bytes=len(data))
            return []
