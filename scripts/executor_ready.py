#!/usr/bin/env python3
"""Executor-side ready request: keep asking the supervisor until it replies.

An executor whose task was disarmed must never wait silently for the supervisor
to notice it. `persist` runs an out-of-turn loop that asks the bound supervisor
for the next task every ASK_INTERVAL seconds and stops only when a reply
actually reaches this executor (a mailbox file or this session's transcript),
when the supervisor dispatches again, or when an operator stops it. The loop
outlives the executor's own turns on purpose: the supervisor can dispatch only
while this executor is NOT inside a turn, so asking must not depend on turns.

Every attempt is an ordinary journaled message (never a task pack, never
force-compose) with its own marker and record, so the idle Stop guard can bind
the obligation to real dispatch evidence instead of a claim. An earlier ask
still pending at the supervisor's own input is never stacked: the loop re-reads
that input first and defers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

PREFIX = 'EXECUTOR_READY'
ASK_INTERVAL = 60.0          # user directive 2026-10-08: re-ask every 60 s, no cap
POLL_SECONDS = 5.0           # loop tick: reply latency, not ask cadence
HEARTBEAT_MAX_AGE = 180.0    # a record older than this is not a live loop
ASK_HISTORY = 20
REPLY_TOKENS = ('claude:identity', 'codex:identity', 'task-pack')
REPLY_HEAD = re.compile(r'\s*(STATUS\s*:|TASK|BLOCKED|WAITING_DEPENDENCY|SOLO|ACK)')
SKIP_PREFIXES = ('This session is being continued', 'Stop hook feedback', '<command-',
                 '<local-command', '[Request interrupted', 'EXECUTOR_IDLE')
ASKING, ANSWERED, STOPPED = 'ASKING', 'ANSWERED', 'STOPPED'


def state_root():
    return Path.home() / '.local/state/multi-agent-collaboration'


def idle_root():
    return state_root() / 'executor-idle-v1'


def requests_dir():
    return idle_root() / 'requests'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def request_key(caller_uuid, marker):
    return digest(json.dumps([caller_uuid, marker], sort_keys=True))


def loop_path(caller_uuid):
    return idle_root() / 'loops' / (digest(caller_uuid) + '.json')


def stop_path(caller_uuid):
    return idle_root() / 'loops' / (digest(caller_uuid) + '.stop.json')


def mailbox_dir(caller_uuid):
    """Pull channel: the supervisor can answer by writing a file, with no TUI."""
    return idle_root() / 'mailbox' / digest(caller_uuid)[:16]


def build_text(executor_ref, marker, task_id='', note='', attempt=1, mailbox=''):
    """One line, marker-bound; the first token can never look like a task dispatch."""
    text = (f'{PREFIX}|{executor_ref}|{marker} ask #{attempt}: previous task '
            f'{task_id or "(unknown)"} is no longer armed; this executor is idle and is '
            'asking again because no reply has reached it yet. Please review this request '
            'and dispatch the next independent task pack (handshake + task-pack). If none '
            'is available, reply WAITING_DEPENDENCY or SOLO with the trigger condition. '
            'Dispatch after this executor turn ends: a running turn makes the bridge-test '
            'pre-read refuse.')
    if mailbox:
        text += (f' Reply to this surface over the bridge, or write {mailbox}/{marker}.json; '
                 f'a reply that stays only in your own thread never reaches this executor, '
                 f'which keeps asking every {int(ASK_INTERVAL)}s until one arrives.')
    note = ' '.join(str(note).split())
    return text + (' ' + note if note else '')


def write_record(caller_uuid, marker, text, target_surface):
    path = requests_dir() / (request_key(caller_uuid, marker) + '.json')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = dict(marker=marker, caller_surface_uuid=caller_uuid, target_surface=target_surface,
                  payload_sha256=digest(text), text=text, created_at_epoch=time.time())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(record, handle, ensure_ascii=False)
    return path, record


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name('.' + path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)


def process_command(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ''
    if pid <= 0:
        return ''
    try:
        out = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout.strip() if out.returncode == 0 else ''


def pid_runs_loop(pid, argv=''):
    """True only when that pid is still running the process that wrote the record.

    A record is a claim. The loop stores its own argv; liveness requires the
    live process to carry it, so a fabricated record pointing at some other
    live pid cannot pass as an ask loop.
    """
    command = process_command(pid)
    if not command:
        return False
    argv = ' '.join(str(argv).split())
    return bool(argv) and argv in ' '.join(command.split())


def loop_live(record, now=None):
    """A loop counts only while it is asking, fresh, bounded and really running."""
    if not isinstance(record, dict) or record.get('state') != ASKING:
        return False
    now = time.time() if now is None else now
    if now - float(record.get('heartbeat_epoch', 0)) > HEARTBEAT_MAX_AGE:
        return False
    if float(record.get('interval_seconds', 1e9)) > ASK_INTERVAL:
        return False
    return pid_runs_loop(record.get('pid'), record.get('argv', ''))


class Tail:
    """Read only bytes appended after construction; transcripts are large."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.carry = b''
        try:
            self.offset = self.path.stat().st_size if self.path else 0
        except OSError:
            self.offset = 0

    def lines(self):
        if not self.path:
            return []
        try:
            with self.path.open('rb') as handle:
                handle.seek(self.offset)
                data = handle.read()
                self.offset += len(data)
        except OSError:
            return []
        parts = (self.carry + data).split(b'\n')
        self.carry = parts.pop()
        return parts


def user_texts(entry):
    """Text this session actually received: real user turns and queued dispatches.

    Tool results, meta entries, hook feedback and compaction summaries are this
    executor's own context, never a supervisor reply.
    """
    texts = []
    if entry.get('type') == 'user' and not entry.get('isMeta'):
        content = (entry.get('message') or {}).get('content')
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts += [b.get('text', '') for b in content
                      if isinstance(b, dict) and b.get('type') == 'text']
    attachment = entry.get('attachment') or {}
    if attachment.get('type') == 'queued_command':
        texts.append(str(attachment.get('prompt', '')))
    return [t for t in texts if t and not t.lstrip().startswith(SKIP_PREFIXES)
            and '<system-reminder>' not in t]


def looks_like_reply(text, markers):
    return (any(m and m in text for m in markers) or any(t in text for t in REPLY_TOKENS)
            or bool(REPLY_HEAD.match(text)))


def find_reply(mailbox, tail, markers, since_epoch):
    """A reply must have reached THIS executor: mailbox file or own transcript."""
    mailbox = Path(mailbox) if mailbox else None
    if mailbox and mailbox.is_dir():
        for path in sorted(mailbox.glob('*.json')):
            try:
                if path.stat().st_mtime < since_epoch:
                    continue
            except OSError:
                continue
            body = read_json(path)
            if body is not None:
                return dict(source='mailbox', path=str(path), status=body.get('status', ''),
                            text=json.dumps(body, ensure_ascii=False)[:800], at_epoch=time.time())
    for line in tail.lines() if tail else []:
        if b'"user"' not in line and b'queued_command' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for text in user_texts(entry):
            if looks_like_reply(text, markers):
                return dict(source='transcript', status='', text=text[:800],
                            at_epoch=time.time())
    return None


def _bridge():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cmux_bridge
    return cmux_bridge


def _submit(bridge, supervisor, text, marker, reconcile_only=False):
    try:
        result = bridge.submit_text(supervisor, text, marker=marker, reconcile_only=reconcile_only)
        return dict(outcome='CONFIRMED', result=result)
    except bridge.DispatchUnconfirmed as exc:
        # Queued or unverified: the original attempt stands. Never resend it.
        return dict(outcome='UNCONFIRMED_DO_NOT_RESEND', detail=str(exc)[:400])


def ask_held(bridge, supervisor, marker, caller_uuid=None):
    """True while the supervisor's own input still holds this ask (never stack)."""
    if not marker:
        return False
    try:
        bridge.pin_workspace(supervisor, caller_uuid=caller_uuid)
        screen = bridge.read_screen(supervisor, lines=200)
    except Exception:
        return False
    if bridge.pending_queue_holds(screen, marker):
        return True
    block = bridge.compose_block_text(screen) or ''
    return ''.join(marker.split()) in ''.join(block.split())


def ask_due(last_ask, now, held, interval=ASK_INTERVAL):
    """ASK, HELD (deferred: the previous ask is still at the input) or WAIT."""
    if not last_ask:
        return 'ASK'
    since = max(float(last_ask.get('at_epoch', 0)), float(last_ask.get('held_epoch', 0)))
    if now - since < interval:
        return 'WAIT'
    return 'HELD' if held() else 'ASK'


def ask_once(bridge, supervisor, caller_uuid, executor_ref, task_id='', note='', attempt=1,
             mailbox='', clock=time.time):
    """One journaled ask with its own marker; failures are recorded, never fatal."""
    marker = f'{PREFIX}_{secrets.token_hex(4)}'
    text = build_text(executor_ref, marker, task_id, note, attempt, mailbox)
    if bridge._looks_like_task_dispatch(text):
        raise RuntimeError('ready request must never look like a task dispatch')
    try:
        bridge.pin_workspace(supervisor, caller_uuid=caller_uuid)
        write_record(caller_uuid, marker, text, supervisor)
        outcome = _submit(bridge, supervisor, text, marker)['outcome']
    except Exception as exc:
        outcome = ('ERROR_' + type(exc).__name__ + ': ' + str(exc))[:300]
    return dict(marker=marker, attempt=attempt, outcome=outcome, at_epoch=clock())


def request(supervisor, task_id='', note=''):
    """One-shot manual ask. The Stop guard requires `persist`, not this."""
    bridge = _bridge()
    proof = bridge.pin_workspace(supervisor)
    caller = proof['caller_surface_uuid']
    executor_ref = os.environ.get('CMUX_SURFACE_ID') or caller
    marker = f'{PREFIX}_{secrets.token_hex(4)}'
    text = build_text(executor_ref, marker, task_id, note, 1, str(mailbox_dir(caller)))
    if bridge._looks_like_task_dispatch(text):
        raise RuntimeError('ready request must never look like a task dispatch')
    path, _record = write_record(caller, marker, text, supervisor)
    return dict(marker=marker, record=str(path), **_submit(bridge, supervisor, text, marker))


def reconcile(supervisor, marker):
    bridge = _bridge()
    caller = bridge.pin_workspace(supervisor)['caller_surface_uuid']
    record = json.loads((requests_dir() / (request_key(caller, marker) + '.json')).read_text())
    return dict(marker=marker,
                **_submit(bridge, supervisor, record['text'], marker, reconcile_only=True))


def run_loop(bridge, supervisor, caller_uuid, executor_ref='', task_id='', note='',
             transcript='', mailbox=None, interval=ASK_INTERVAL, poll=POLL_SECONDS,
             max_ticks=None, clock=time.time, sleep=time.sleep):
    """Ask every `interval` until a reply reaches this executor. No cap, no timeout.

    The record is written before anything that can fail, so a Stop guard can see
    a live loop even while the bridge itself is refusing. Only a real reply, a
    dispatch signal or an operator stop ends it.
    """
    interval = max(1.0, min(float(interval), ASK_INTERVAL))
    mailbox = Path(mailbox) if mailbox else mailbox_dir(caller_uuid)
    mailbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    signal = stop_path(caller_uuid)
    try:
        signal.unlink()
    except OSError:
        pass
    path, started = loop_path(caller_uuid), clock()
    record = dict(pid=os.getpid(), argv=' '.join(sys.argv),
                  caller_surface_uuid=caller_uuid, supervisor=supervisor,
                  executor_ref=executor_ref or caller_uuid, task_id=task_id,
                  mailbox=str(mailbox), transcript=str(transcript or ''),
                  interval_seconds=interval, poll_seconds=float(poll), started_epoch=started,
                  heartbeat_epoch=started, state=ASKING, ask_count=0, asks=[], reply=None,
                  stop=None, last_error=None)
    write_json(path, record)
    tail, ticks = Tail(transcript) if transcript else None, 0
    while record['state'] == ASKING and (max_ticks is None or ticks < max_ticks):
        now = clock()
        record['heartbeat_epoch'] = now
        try:
            stop = read_json(signal)
            if stop is not None:
                record.update(state=STOPPED, stop=dict(stop, at_epoch=now))
                break
            markers = [a.get('marker', '') for a in record['asks']]
            reply = find_reply(mailbox, tail, markers, started)
            if reply:
                record.update(state=ANSWERED, reply=reply, ended_epoch=now)
                break
            last = record['asks'][-1] if record['asks'] else None
            verdict = ask_due(last, now, lambda: ask_held(
                bridge, supervisor, (last or {}).get('marker', ''), caller_uuid), interval)
            if verdict == 'HELD':
                last['held_epoch'] = now
                last['held_observations'] = int(last.get('held_observations', 0)) + 1
            elif verdict == 'ASK':
                record['ask_count'] += 1
                record['asks'] = (record['asks'] + [ask_once(
                    bridge, supervisor, caller_uuid, record['executor_ref'], task_id, note,
                    record['ask_count'], str(mailbox), clock)])[-ASK_HISTORY:]
        except Exception as exc:  # a loop that dies is a dead wait; record and retry
            record['last_error'] = (type(exc).__name__ + ': ' + str(exc))[:300]
        write_json(path, record)
        ticks += 1
        if record['state'] == ASKING and (max_ticks is None or ticks < max_ticks):
            sleep(poll)
    record['heartbeat_epoch'] = clock()
    write_json(path, record)
    return record


def persist(supervisor, caller_uuid=None, task_id='', note='', transcript='', mailbox='',
            interval=ASK_INTERVAL, poll=POLL_SECONDS, max_ticks=None):
    bridge = _bridge()
    caller = caller_uuid or bridge.pin_workspace(supervisor)['caller_surface_uuid']
    executor_ref = os.environ.get('CMUX_SURFACE_ID') or caller
    existing = read_json(loop_path(caller))
    if existing and existing.get('pid') != os.getpid() and loop_live(existing):
        return dict(outcome='ALREADY_RUNNING', pid=existing.get('pid'),
                    started_epoch=existing.get('started_epoch'))
    record = run_loop(bridge, supervisor, caller, executor_ref, task_id, note, transcript,
                      mailbox or None, interval, poll, max_ticks)
    return dict(outcome=record['state'], asks=record['ask_count'], reply=record.get('reply'),
                stop=record.get('stop'), record=str(loop_path(caller)))


def stop_loop(caller_uuid, reason='OPERATOR'):
    """Operator kill switch (and the guard's dispatch signal). Always reachable."""
    write_json(stop_path(caller_uuid), dict(reason=reason, requested_at_epoch=time.time()))
    return dict(outcome='STOP_REQUESTED', reason=reason, caller_surface_uuid=caller_uuid)


def status(caller_uuid):
    record = read_json(loop_path(caller_uuid))
    if not record:
        return dict(outcome='NO_LOOP_RECORD', caller_surface_uuid=caller_uuid)
    return dict(outcome=record.get('state'), live=loop_live(record), pid=record.get('pid'),
                asks=record.get('ask_count'), interval_seconds=record.get('interval_seconds'),
                heartbeat_age=round(time.time() - float(record.get('heartbeat_epoch', 0)), 1),
                last_ask=(record.get('asks') or [None])[-1], reply=record.get('reply'),
                stop=record.get('stop'), last_error=record.get('last_error'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    req = sub.add_parser('request')
    req.add_argument('--supervisor', required=True)
    req.add_argument('--task', default='')
    req.add_argument('--note', default='')
    rec = sub.add_parser('reconcile')
    rec.add_argument('--supervisor', required=True)
    rec.add_argument('--marker', required=True)
    per = sub.add_parser('persist')
    per.add_argument('--supervisor', required=True)
    per.add_argument('--caller-uuid', default='')
    per.add_argument('--task', default='')
    per.add_argument('--note', default='')
    per.add_argument('--transcript', default='')
    per.add_argument('--mailbox', default='')
    per.add_argument('--interval', type=float, default=ASK_INTERVAL)
    per.add_argument('--poll', type=float, default=POLL_SECONDS)
    per.add_argument('--max-ticks', type=int, default=None)
    stp = sub.add_parser('stop')
    stp.add_argument('--caller-uuid', required=True)
    stp.add_argument('--reason', default='OPERATOR')
    sta = sub.add_parser('status')
    sta.add_argument('--caller-uuid', required=True)
    args = parser.parse_args(argv)
    if args.mode == 'request':
        out = request(args.supervisor, args.task, args.note)
    elif args.mode == 'reconcile':
        out = reconcile(args.supervisor, args.marker)
    elif args.mode == 'persist':
        out = persist(args.supervisor, args.caller_uuid or None, args.task, args.note,
                      args.transcript, args.mailbox, args.interval, args.poll, args.max_ticks)
    elif args.mode == 'stop':
        out = stop_loop(args.caller_uuid, args.reason)
    else:
        out = status(args.caller_uuid)
    print(json.dumps(out, ensure_ascii=False, default=str))
    if args.mode in ('request', 'reconcile'):
        print('End this turn now; the supervisor owns the next dispatch or disposition.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
