"""Owned Word operations and cross-process serialization (no foreground activation)."""
from __future__ import annotations
import json
import os
import time
import threading
from contextlib import contextmanager
from pathlib import Path

_HELD = threading.local()


def parse_document_probe(returncode, stdout, stderr, *, sender):
    """Apple Events 被拒绝时保留 UNKNOWN，不能以 AX 零窗口代替文档数。"""
    denied = "-1743" in stderr
    documents = int(stdout.strip()) if returncode == 0 and stdout.strip().isdigit() else None
    return {"sender": sender, "documents": documents, "documents_status": "KNOWN" if documents is not None else "UNKNOWN",
            "error_code": -1743 if denied else returncode if returncode else None,
            "raw_stdout": stdout, "raw_stderr": stderr,
            "native_write_ready": documents == 0 and returncode == 0}


def word_pending_path():
    return Path.home() / '.cache/wordrev/word-automation.pending.json'


def mark_word_pending(state):
    target = word_pending_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(pid=os.getpid(), at=time.time(), **state), indent=2))
    temporary.replace(target)


def clear_word_pending():
    """Acknowledge an interrupted run only after Word is known to be idle."""
    with word_lock('pending-recovery', _allow_pending=True):
        return _clear_word_pending_locked()


def _clear_word_pending_locked():
    target = word_pending_path()
    if not target.exists():
        return None
    import subprocess
    if subprocess.run(["/usr/bin/pgrep", "-x", "Microsoft Word"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        probe = subprocess.run(
            ["/usr/bin/osascript", "-e",
             'tell application "Microsoft Word" to get name of every document'],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode != 0 or probe.stdout.strip() not in ("", "missing value"):
            raise RuntimeError("WORD_RECOVERY_REQUIRED: Word still has an open or unreadable document")
    state = json.loads(target.read_text())
    target.unlink()
    return state


@contextmanager
def word_lock(owner: str, timeout: float = 60, path: Path | None = None, *, _allow_pending=False):
    import fcntl
    if isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("Word lock timeout must be positive")
    path = path or Path.home() / ".cache/wordrev/word-automation.lock"
    path = path.resolve()
    shared = path == (Path.home() / ".cache/wordrev/word-automation.lock").resolve()
    key = (os.getpid(), str(path))
    held = getattr(_HELD, "paths", set())
    if key in held:
        # The adapter and Runner may nest in the same synchronous thread.
        # Other threads/processes still acquire flock on this same inode.
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.seek(0)
                    raise TimeoutError(f"Word lock timeout after {timeout}s; owner={handle.read()}; lock={path}")
                time.sleep(.1)
        if shared and not _allow_pending and word_pending_path().exists():
            raise RuntimeError('WORD_RECOVERY_REQUIRED: ' + word_pending_path().read_text())
        handle.seek(0); handle.truncate()
        json.dump({"pid": os.getpid(), "owner": owner, "started": time.time()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
        _HELD.paths = held | {key}
        try:
            yield
        finally:
            _HELD.paths = held
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def owned_script(path: Path, operation: str, output: Path | None = None) -> str:
    """Zotero's VBA launcher is asynchronous: await a document change, not macro return."""
    p, name = json.dumps(str(path)), json.dumps(path.name)
    action = {
        "refresh": '''activate object targetDocument
save targetDocument
run VB macro macro name "ZoteroRefresh"
set refreshObserved to false
repeat 160 times
try
if saved of targetDocument is false then
set refreshObserved to true
exit repeat
end if
end try
delay 0.25
end repeat
if not refreshObserved then error "Zotero launcher returned but no field update was observed; keep document for inspection"
error "Zotero update observed, but dirty state is not completion proof. Keep owned document open for native UI and field verification before save/close."
''',
        "accept": 'accept all revisions targetDocument\n',
        "save": '',
        "pdf": f'set print revisions of targetDocument to false\nset show revisions of targetDocument to false\nsave as targetDocument file name {json.dumps(str(output))} file format format PDF\n',
    }[operation]
    return f'''tell application "Microsoft Word"
with timeout of 90 seconds
set targetDocument to missing value
set previousAlerts to display alerts
try
set display alerts to alerts none
open POSIX file {p}
repeat 160 times
try
set targetDocument to document {name}
if targetDocument is not missing value then exit repeat
end try
delay 0.25
end repeat
if targetDocument is missing value then error "Owned Word document did not become accessible"
{action}
{'save targetDocument' if operation != 'pdf' else ''}
set closeCompleted to false
repeat 120 times
try
if not (exists document {name}) then
set closeCompleted to true
exit repeat
end if
close document {name} saving {'yes' if operation != 'pdf' else 'no'}
set closeCompleted to true
exit repeat
on error closeMessage number closeNumber
if closeNumber is not -1708 and closeNumber is not -1728 then error closeMessage number closeNumber
end try
delay 0.25
end repeat
if not closeCompleted then error "Owned Word document remained busy after operation"
set display alerts to previousAlerts
return "WORDREV_OWNED_OPERATION_COMPLETE"
on error errMsg number errNum
set display alerts to previousAlerts
error errMsg number errNum
end try
end timeout
end tell'''


def operate(run, source: Path, operation: str, output: Path | None = None):
    result = run(["osascript", "-e", owned_script(source, operation, output)], timeout=110)
    if "WORDREV_OWNED_OPERATION_COMPLETE" not in result.stdout:
        raise RuntimeError(f"Word operation incomplete: {operation}: {source}")
    expected = output if operation == "pdf" else source
    if not expected or not expected.is_file() or expected.stat().st_size == 0:
        raise RuntimeError(f"Word output absent/empty: {expected}")
    return result
