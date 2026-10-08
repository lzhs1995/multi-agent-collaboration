"""Test-only helpers: a fake bridge for the detached idle pusher, and cleanup.

Product code never imports this module. The fake bridge only appends calls to a
JSONL file beside itself, so a pusher spawned by a test can never reach a real
cmux surface.
"""
import json
import os
from pathlib import Path
import signal
import time

FAKE = '''import json, pathlib
LOG = pathlib.Path(__file__).with_suffix(".calls.jsonl")
def submit_text(surface, text, marker=None, **_kw):
    with LOG.open("a") as h:
        h.write(json.dumps(dict(surface=surface, text=text, marker=marker)) + "\\n")
    return {"confirmed": False, "delivery_state": "FAKE"}
'''


def install(directory, env):
    """Write the fake bridge and point the pusher at it; returns the calls log path."""
    path = Path(directory) / 'fake_bridge.py'
    path.write_text(FAKE)
    env['CMUX_IDLE_PUSH_BRIDGE'] = str(path)
    return path.with_suffix('.calls.jsonl')


def calls(log, wait=5.0, count=1):
    deadline = time.time() + wait
    while time.time() < deadline:
        if log.exists():
            rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
            if len(rows) >= count:
                return rows
        time.sleep(0.05)
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def supervisor_answers(home):
    """Stand in for the authenticated supervisor ack (its auth is tested in test_idle_pull)."""
    import hashlib
    root = Path(home) / '.local/state/multi-agent-collaboration/idle-requests-v1'
    for req in root.glob('*/*.json'):
        if req.name.endswith('.ack.json'):
            continue
        sha = hashlib.sha256(req.read_bytes()).hexdigest()
        req.with_name(req.stem + '.ack.json').write_text(json.dumps(dict(
            request_sha256=sha, supervisor_uuid='TEST', reason='WAITING_DEPENDENCY: test')))


def kill_pushers(home):
    """Terminate every pusher recorded under a private HOME."""
    root = Path(home) / '.local/state/multi-agent-collaboration/idle-push-v1'
    for status in root.glob('*/*/status.json'):
        try:
            os.kill(int(json.loads(status.read_text())['pid']), signal.SIGTERM)
        except (OSError, ValueError, KeyError):
            pass
    time.sleep(0.1)
