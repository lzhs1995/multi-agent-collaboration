"""Staged macOS Word PDF export, serialized with all wordrev operations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from word_runtime import word_lock


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def apple_string(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


class StageError(RuntimeError):
    def __init__(self, stage, message, uncertain=False):
        super().__init__(message)
        self.stage = stage
        self.uncertain = uncertain


class ExportSession:
    def __init__(self, source, output, *, runner=subprocess.run):
        self.source = Path(source).resolve(strict=True)
        self.output = Path(output).absolute()
        self.runner = runner
        self.run_id = uuid.uuid4().hex
        self.evidence = self.output.parent / (self.output.stem + '.word-run-' + self.run_id[:8])
        self.evidence.mkdir(parents=True, exist_ok=False)
        # Word can show an unanswerable sandbox bookmark prompt for an arbitrary
        # output directory, even with Full Disk Access. Render inside its own
        # container; Python publishes the validated file outside that container.
        self.staging = (Path.home() / 'Library/Containers/com.microsoft.Word/Data'
                        / 'Documents/wordrev-pdf' / self.run_id)
        self.owned = self.staging / ('wordrev_' + self.run_id[:12] + '.docx')
        self.temporary = self.staging / 'export.pdf'
        self.events = []
        self.result = dict(success=False, backend='word-applescript', run_id=self.run_id,
                           input=str(self.source), input_sha256=sha256(self.source),
                           pdfPath=str(self.output), evidence_dir=str(self.evidence),
                           staging_dir=str(self.staging), owned_document=str(self.owned),
                           service_sha256=sha256(__file__),
                           runtime_sha256=sha256(Path(__file__).with_name('word_runtime.py')))

    def record(self, stage, status, **values):
        event = dict(stage=stage, status=status, at=time.time(), **values)
        self.events.append(event)
        with (self.evidence / 'stages.jsonl').open('a') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')

    def apple(self, stage, body, seconds=20):
        script = ('with timeout of ' + str(seconds) + ' seconds\n'
                  'tell application "Microsoft Word"\n' + body + '\n'
                  'end tell\nend timeout\n')
        script_path = self.evidence / (stage.lower() + '.applescript')
        script_path.write_text(script)
        self.record(stage, 'START', budget_seconds=seconds)
        start = time.monotonic()
        try:
            proc = self.runner(['/usr/bin/osascript', str(script_path)], capture_output=True,
                               text=True, timeout=seconds + 30)
        except subprocess.TimeoutExpired as exc:
            self.record(stage, 'TIMEOUT_UNRESOLVED', duration=time.monotonic()-start)
            raise StageError(stage, str(exc), True) from exc
        self.record(stage, 'PASS' if proc.returncode == 0 else 'FAIL',
                    duration=time.monotonic()-start, returncode=proc.returncode,
                    stdout=proc.stdout, stderr=proc.stderr)
        if proc.returncode:
            raise StageError(stage, proc.stderr, '-1712' in proc.stderr)
        return proc.stdout.strip()

    def binding(self):
        return ('set docRef to document ' + apple_string(self.owned.name) + '\n'
                'if (posix full name of docRef) is not ' + apple_string(self.owned) +
                ' then error "Owned document path mismatch"\n')

    def verify(self):
        self.record('VERIFY', 'START')
        if not self.temporary.is_file() or self.temporary.read_bytes()[:5] != b'%PDF-':
            raise StageError('VERIFY', 'No valid PDF header')
        info = self.runner(['pdfinfo', str(self.temporary)], capture_output=True, text=True, timeout=30)
        txt = self.runner(['pdftotext', '-layout', str(self.temporary), '-'], capture_output=True, text=True, timeout=30)
        if info.returncode or txt.returncode or not txt.stdout.strip():
            raise StageError('VERIFY', 'PDF parsing/text extraction failed')
        metadata = dict(line.split(':', 1) for line in info.stdout.splitlines() if ':' in line)
        pages = int(metadata.get('Pages', '0').strip())
        if pages < 1:
            raise StageError('VERIFY', 'PDF has no pages')
        (self.evidence / 'pdfinfo.txt').write_text(info.stdout)
        (self.evidence / 'text.txt').write_text(txt.stdout)
        self.result.update(pages=pages, bytes=self.temporary.stat().st_size,
                           pdf_sha256=sha256(self.temporary),
                           text_sha256=hashlib.sha256(txt.stdout.encode()).hexdigest())
        if sha256(self.source) != self.result['input_sha256']:
            raise StageError('VERIFY', 'Source changed during export')
        # Copy across filesystems, then link for atomic no-clobber publication.
        publishing = self.output.parent / ('.' + self.output.name + '.' + self.run_id)
        try:
            shutil.copyfile(self.temporary, publishing)
            if sha256(publishing) != self.result['pdf_sha256']:
                raise StageError('VERIFY', 'Published PDF hash mismatch')
            os.link(publishing, self.output)
        finally:
            publishing.unlink(missing_ok=True)
        self.record('VERIFY', 'PASS', pages=pages, pdf_sha256=self.result['pdf_sha256'])

    def export(self):
        if self.output.exists():
            self.result.update(code='OUTPUT_EXISTS', error='Existing PDF preserved; choose a new path')
            return self.finish()
        for command in ['pdfinfo', 'pdftotext']:
            if not shutil.which(command):
                self.result.update(code='PDF_VALIDATOR_MISSING', error=command)
                return self.finish()
        opened = False
        try:
            with word_lock('word-pdf:' + self.run_id):
                try:
                    self.staging.mkdir(parents=True, exist_ok=False)
                    shutil.copy2(self.source, self.owned)
                    if sha256(self.owned) != self.result['input_sha256']:
                        raise StageError('STAGING', 'Staging hash mismatch')
                    self.record('STAGING', 'PASS', path=str(self.owned),
                                sha256=self.result['input_sha256'])
                    probe = self.apple('PROBE', 'return {version, name of every document}')
                    self.result['word_probe'] = probe
                    # Check identity before opening; other documents need not be closed.
                    self.apple('PREFLIGHT', 'if exists document ' + apple_string(self.owned.name) +
                               ' then error "TARGET_ALREADY_OPEN"\nreturn "READY"')
                    lock_names = ['~$' + self.source.name, '~$' + self.source.name[2:]]
                    for name in lock_names:
                        if self.source.with_name(name).exists():
                            raise StageError('PREFLIGHT', 'TARGET_LOCK_PRESENT: ' + name)
                    # Word's `open` accepts a POSIX file object directly.  Keeping
                    # this separate from path binding avoids resolving another open
                    # document with the same basename.
                    self.apple('OPEN', 'open (POSIX file ' + apple_string(self.owned) +
                               ') read only true add to recent files false', 180)
                    opened = True
                    self.apple('RESOLVE_PATH', self.binding() + 'return posix full name of docRef')
                    self.apple('EXPORT', self.binding() +
                               'set show revisions of docRef to false\n'
                               'set print revisions of docRef to false\n'
                               'set outputFile to ' + apple_string(self.temporary) + '\n'
                               'save as docRef file name outputFile file format format PDF add to recent files false', 180)
                    self.verify()
                    self.result.update(success=True, code='EXPORT_PASS', cleanup='PENDING')
                    self.apple('CLOSE', self.binding() + 'close docRef saving no', 30)
                    self.result['cleanup'] = 'PASS'
                    self.result['staged_docx_unchanged'] = sha256(self.owned) == self.result['input_sha256']
                    shutil.rmtree(self.staging)
                except Exception as error:
                    exc = error if isinstance(error, StageError) else StageError(
                        self.events[-1]['stage'] if self.events else 'STAGING', str(error))
                    self.result.update(failed_stage=exc.stage, error=str(exc))
                    if self.result['success']:
                        self.result.update(code='EXPORT_PASS_CLEANUP_PENDING', cleanup='PENDING')
                    else:
                        self.result['code'] = 'TIMEOUT_UNRESOLVED' if exc.uncertain else 'STAGE_FAILED'
                    if exc.uncertain or opened:
                        from word_runtime import mark_word_pending
                        mark_word_pending(dict(run_id=self.run_id, phase=exc.stage,
                                               source=str(self.source), owned_document=str(self.owned),
                                               evidence_dir=str(self.evidence),
                                               status='TIMEOUT_UNRESOLVED' if exc.uncertain else 'CLEANUP_PENDING'))
                    self.result['permissionRequired'] = '-1743' in str(exc)
                    # No close, retry, global alerts change, or app termination after failure.
        except Exception as exc:
            self.result.update(code='WORD_LOCK_OR_RUNTIME_ERROR', error=str(exc))
        return self.finish()

    def finish(self):
        self.result['input_unchanged'] = sha256(self.source) == self.result['input_sha256']
        if not self.result['input_unchanged']:
            self.result.update(success=False, code='SOURCE_CHANGED')
        (self.evidence / 'result.json').write_text(json.dumps(self.result, ensure_ascii=False, indent=2)+'\n')
        return self.result


def export_pdf(source, output):
    return ExportSession(source, output).export()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', nargs='?')
    parser.add_argument('output', nargs='?')
    parser.add_argument('--ack-pending', action='store_true',
                        help='clear an interrupted run only when Word has no open documents')
    args = parser.parse_args()
    if args.ack_pending:
        from word_runtime import clear_word_pending
        try:
            print(json.dumps(dict(success=True, code='PENDING_ACKNOWLEDGED',
                                  pending=clear_word_pending()), ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            print(json.dumps(dict(success=False, code='RECOVERY_REQUIRED', error=str(exc)),
                             ensure_ascii=False, indent=2))
            return 1
    if not args.source or not args.output:
        parser.error('source and output are required unless --ack-pending is used')
    try:
        result = export_pdf(args.source, args.output)
    except Exception as exc:
        result = dict(success=False, code='INVALID_INPUT', error=str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('success') and result.get('cleanup') == 'PASS' and result.get('input_unchanged') else 1


if __name__ == '__main__':
    raise SystemExit(main())
