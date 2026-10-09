#!/usr/bin/env python3
"""空闲 executor 每 60 秒主动求派，直到 Codex 主管答复（用户 2026-10-09 明令）。

每一轮都是带新 marker 的新请求，从不重贴旧消息：
- 文件通道：每轮覆盖写 <channel>/EXECUTOR_REASK_<caller8>_CURRENT.json（主管轮内也能读）；
- 终端通道：仅当主管 composer 为空、且本段没有仍排队/卡在 compose 的旧请求时，
  才经 executor_ready 的 journaled bridge 发送一次；否则只走文件通道，绝不堵 composer。
答复必须精确匹配 caller/supervisor/task/episode/marker；无关 user turn 不算。
只有绑定答复、已认证的新派发或 operator stop 结束一段；配套 Stop hook
（cmux_executor_reask_stop_guard.py）在段内拦住结束，要求继续 run。
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import time

import executor_ready as ready
import executor_reply as replies

INTERVAL = 60.0
POLL = 5.0
MAX_RUN_SECONDS = 540.0
MAX_TEXT = ready.prompt_reference.MAX_INLINE_BYTES  # 保守的 UTF-8 字节预算，非通用 UI 字数阈值。
HISTORY = 20
CHANNEL_SCAN = 64
CHANNEL_FILE_BYTES = 256 * 1024
WAITING, ANSWERED, CONSUMED, STOPPED = 'WAITING_REPLY', 'ANSWERED', 'CONSUMED', 'STOPPED'
HELD = ('QUEUED', 'COMPOSE')


def root():
    return ready.state_root() / 'executor-reask-v1'


def state_path(caller):
    return root() / (ready.digest(caller) + '.json')


def stop_file(caller):
    return root() / (ready.digest(caller) + '.stop.json')


def config_path():
    return root() / 'config.json'


def load(caller):
    return ready.read_json(state_path(caller))


def save(state):
    ready.write_json(state_path(state['caller_surface_uuid']), state)


@contextmanager
def episode_lock(caller):
    """Serialize CLI and automatic Stop; a busy hook never waits on UI I/O."""
    root().mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root() / (ready.digest(caller) + '.lock'),
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def inbox_path(state):
    return (ready.state_root() / 'supervisor-inbox-v1' / ready.digest(state['supervisor'])
            / (ready.digest(state['caller_surface_uuid']) + '.json'))


def idle_binding(caller, workspace=''):
    from cmux_executor_idle_guard import _binding_path, bindings_dir
    if workspace:
        body = ready.read_json(_binding_path(workspace, caller))
        return body if body and body.get('surface_uuid') == caller and body.get('workspace_uuid') == workspace else None
    # Legacy explicit episodes may lack workspace. Do not pick between matches.
    found = []
    try:
        for index, path in enumerate(bindings_dir().iterdir()):
            if index >= 256:
                return None
            body = ready.read_json(path)
            if body and body.get('surface_uuid') == caller:
                found.append(body)
    except OSError:
        return None
    return found[0] if len(found) == 1 else None


def current_name(caller):
    return f'EXECUTOR_REASK_{caller[:8]}_CURRENT.json'


def configured_channels(supervisor):
    conf = ready.read_json(config_path()) or {}
    value = (conf.get('channels') or {}).get(supervisor, [])
    return [str(v) for v in value if isinstance(v, str)] if isinstance(value, list) else []


def start(caller, supervisor, task_id='', transcript='', channels=None, executor_ref='',
          now=None, reason='MANUAL', own_prefixes=(), workspace='', purpose='REQUEST_NEXT_TASK',
          resume_stopped=False):
    with episode_lock(caller):
        return start_locked(caller, supervisor, task_id, transcript, channels, executor_ref,
                            now, reason, own_prefixes, workspace, purpose, resume_stopped)


def start_locked(caller, supervisor, task_id='', transcript='', channels=None, executor_ref='',
                 now=None, reason='MANUAL', own_prefixes=(), workspace='',
                 purpose='REQUEST_NEXT_TASK', resume_stopped=False):
    """开一段新的求派；已在等待的同一主管段原样保留（不重置计数/期限）。"""
    now = time.time() if now is None else now
    old = load(caller)
    if old and old.get('state') == WAITING and old.get('supervisor') == supervisor:
        return old
    if (stop_file(caller).exists() or old and old.get('state') == STOPPED) and not resume_stopped:
        return old or dict(caller_surface_uuid=caller, state=STOPPED, ask_count=0)
    if purpose not in ('REQUEST_NEXT_TASK', 'PENDING_TASK_REPLY'):
        raise ValueError('unknown inquiry purpose')
    try:
        os.unlink(stop_file(caller))
    except FileNotFoundError:
        pass
    tail = None
    if transcript:
        try:
            tail = ready.Tail(transcript).position()
        except (OSError, ValueError):
            transcript = ''
    chans = list(channels) if channels else configured_channels(supervisor)
    state = dict(version=2, caller_surface_uuid=caller, executor_ref=executor_ref or caller,
                 workspace_uuid=workspace, purpose=purpose,
                 supervisor=supervisor, task_id=task_id, state=WAITING, reason=reason,
                 started_epoch=now, episode_id=ready.digest(json.dumps([caller, supervisor, now])),
                 marker_secret=secrets.token_hex(16),
                 transcript=transcript, tail_position=tail, channels=chans,
                 mailbox=str(ready.mailbox_dir(caller)), ask_count=0, asks=[],
                 terminal_sends=0, terminal_attempts=0, native_receipts=0, file_posts=0,
                 check_count=0, attempt_count=0, terminal_pending=None,
                 reply=None, own_prefixes=list(own_prefixes))
    Path(state['mailbox']).mkdir(parents=True, exist_ok=True, mode=0o700)
    save(state)
    return state


def _recent_files(directory, since, limit=CHANNEL_SCAN):
    try:
        entries = [e for e in os.scandir(directory) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    fresh = []
    for e in entries:
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if st.st_mtime >= since and st.st_size <= CHANNEL_FILE_BYTES:
            fresh.append((st.st_mtime, Path(e.path)))
    return [p for _m, p in sorted(fresh, reverse=True)[:limit]]


def issue_marker(state, ordinal):
    """Bounded state can still authenticate a late reply to any issued inquiry."""
    signature = hmac.new(state['marker_secret'].encode(), str(ordinal).encode(),
                         hashlib.sha256).hexdigest()[:16]
    return f'{ready.PREFIX}_{ordinal:x}_{signature}'


def issued_marker(state, marker):
    if not isinstance(marker, str) or len(marker) > 160 or not state.get('marker_secret'):
        return False
    try:
        prefix, ordinal_hex, _signature = marker.rsplit('_', 2)
        ordinal = int(ordinal_hex, 16)
        return (prefix == ready.PREFIX and 0 < ordinal <= state.get('attempt_count', 0)
                and hmac.compare_digest(marker, issue_marker(state, ordinal)))
    except (ValueError, TypeError):
        return False


def find_reply(state):
    """主管答复的三种来源；本人写的 CURRENT 和本人文件不算。"""
    caller, since = state['caller_surface_uuid'], float(state['started_epoch'])
    markers = [a['marker'] for a in state.get('asks', []) if isinstance(a, dict) and a.get('marker')]
    pending = state.get('terminal_pending') or {}
    if pending.get('marker') and pending['marker'] not in markers:
        markers.append(pending['marker'])
    binding = dict(caller=caller, supervisor=state['supervisor'],
                   task_id=state.get('task_id', ''), episode_id=state['episode_id'])
    def matched(body):
        allowed = markers
        if isinstance(body, dict) and issued_marker(state, body.get('marker')):
            allowed = [body['marker']]
        return replies.valid(body, allowed, **binding)
    for path in _recent_files(state['mailbox'], since):
        body = ready.read_json(path)
        if matched(body):
            return dict(source='mailbox', path=str(path), status=body['status'],
                        text=json.dumps(body, ensure_ascii=False)[:800])
    own = tuple([current_name(caller), 'EXECUTOR_REASK_'] + list(state.get('own_prefixes', [])))
    for channel in state.get('channels', []):
        for path in _recent_files(channel, since):
            if path.name.startswith(own) or path.suffix not in ('.json', '.md', '.txt'):
                continue
            try:
                text = path.read_text(errors='replace')
            except OSError:
                continue
            body = replies.from_text(text)
            if matched(body):
                return dict(source='channel', path=str(path), marker=body['marker'],
                            status=body['status'], text=text[:800])
    if state.get('transcript') and state.get('tail_position'):
        try:
            tail = ready.Tail(state['transcript'], state['tail_position'])
        except (OSError, ValueError) as exc:
            state['transcript_error'] = str(exc)[:200]
            return None
        for line in tail.lines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            texts = ready.user_texts(entry) if isinstance(entry, dict) else []
            for text in texts:
                body = replies.from_text(text)
                if matched(body):
                    state['tail_position'] = tail.position()
                    return dict(source='transcript', text=text[:800], status=body['status'])
        state['tail_position'] = tail.position()
    return None


def build_text(state, marker, attempt):
    mailbox = state['mailbox']
    text = (f"{ready.PREFIX}|{state['executor_ref']}|{marker} ask #{attempt}"
            f" (every {int(INTERVAL)}s until you reply): executor "
            f"{state['caller_surface_uuid'][:8]} requests {state.get('purpose', 'REQUEST_NEXT_TASK')}"
            + (f" after {state['task_id']}" if state.get('task_id') else '')
            + ". Reply to this inquiry or dispatch an authorized task; this does not repeat a callback. "
            f"Write {mailbox}/{marker}.json with the exact bindings and a concrete trigger: "
            + json.dumps(replies.template(state, marker), ensure_ascii=False))
    # 全文不截断。write_record 会为超过字节预算的新请求固定正文并发送短通知；
    # 此处只规划校验，文件通道仍记录完整请求，旧 attempt 不迁移。
    ready.prompt_reference.plan(text, marker, ready.state_root() / 'message-bodies-v1')
    return text


def write_channels(state, marker, text, now, status=WAITING):
    written, errors = [], []
    paths = [inbox_path(state)] + [Path(c) / current_name(state['caller_surface_uuid'])
                                  for c in state.get('channels', [])]
    for path in dict.fromkeys(paths):
        try:
            ready.write_json(path, dict(
                schema='executor-reask-current-v2', status=status, at_epoch=now,
                episode_id=state['episode_id'], workspace_uuid=state.get('workspace_uuid', ''),
                purpose=state.get('purpose', 'REQUEST_NEXT_TASK'),
                at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                caller_surface_uuid=state['caller_surface_uuid'], supervisor=state['supervisor'],
                task_id=state.get('task_id', ''), ask_count=state['ask_count'], marker=marker,
                request=text, reply_mailbox=f"{state['mailbox']}/{marker}.json",
                reply_template=replies.template(state, marker),
                native_delivery_confirmed=False,
                terminal_inputs_this_round=0 if status != WAITING else None))
            written.append(str(path))
        except OSError as exc:
            errors.append(dict(path=str(path), error_type=type(exc).__name__))
    return dict(written=written, errors=errors)


def _unreceived_on_screen(bridge, supervisor, caller, ask, flat_screen):
    """历史 marker 无回执只表示未核实，不能据此宣布排队或发送成功。

    已原生收到的请求也会留在屏幕历史区；原次回执优先。
    未核实时保守只走文件通道，实际队列由同版 bridge 单独识别。
    """
    marker = ask.get('marker', '')
    if not marker or ask.get('outcome') == 'CONFIRMED':
        return False
    if marker not in flat_screen:
        return False
    try:
        original = ready._original_request(caller, supervisor, marker)
        receipt = ready._receipt(bridge, supervisor, original['text'], marker)
    except Exception:
        return True                  # 无法核实时按仍待投递处理：宁可只走文件通道
    if receipt:
        ask.update(outcome='CONFIRMED', receipt=receipt)
        return False
    return True


def _held(bridge, supervisor, asks, caller=''):
    """旧请求仍排队或停在 composer：不再粘贴，避免堵住主管和其他 agent。"""
    screen = bridge.read_screen(supervisor, lines=120)
    flat = ''.join(screen.split())   # 长行折行会拆开 marker
    for ask in asks:
        marker = ask.get('marker', '')
        if not ask.get('terminal'):
            continue
        if bridge.pending_queue_holds(screen, marker):
            return 'QUEUED', screen
        if caller and _unreceived_on_screen(bridge, supervisor, caller, ask, flat):
            return 'UNVERIFIED', screen
    if not bridge.compose_block_is_empty(screen):
        return 'COMPOSE', screen
    if bridge.receiver_cannot_submit_now(screen):
        return 'BUSY', screen
    return None, screen


def default_send(state, marker, text):
    """一次 journaled 发送；结果按 executor_ready 原样保留，不重试。"""
    bridge = ready._bridge()
    pending = state.get('terminal_pending') or {}
    if pending.get('marker') and pending['marker'] != marker:
        # An uncertain original attempt stays pinned even when the short history
        # rolls over. Reconciliation is read-only; no repaste or recovery key.
        ready.advance_ask(bridge, state['supervisor'], state['caller_surface_uuid'], pending)
        if pending.get('outcome') != 'CONFIRMED':
            return dict(outcome='SKIPPED_UNVERIFIED', terminal=False)
        state['terminal_pending'] = None
        state['native_receipts'] = state.get('native_receipts', 0) + 1
    held, _screen = _held(bridge, state['supervisor'], state.get('asks', []),
                          state['caller_surface_uuid'])
    if held:
        return dict(outcome='SKIPPED_' + held, terminal=False)
    state['terminal_pending'] = dict(marker=marker, terminal=True, outcome='INTENT_UNRESOLVED')
    save(state)
    ready.write_record(state['caller_surface_uuid'], marker, text, state['supervisor'],
                       state.get('task_id', ''), state['episode_id'])
    ask = dict(marker=marker)
    ready.advance_ask(bridge, state['supervisor'], state['caller_surface_uuid'], ask,
                      allow_send=True)
    journal = ready.state_root() / 'message-dispatch-v1' / ready.request_key(state['caller_surface_uuid'], marker)
    from cmux_evidence_io import attempt_paths
    phases = []
    if journal.is_dir():
        for path in attempt_paths(journal):
            record = ready.read_json(path) or {}
            phases.extend(e.get('phase') for e in record.get('events', []) if isinstance(e, dict))
    result = dict(outcome=ask.get('outcome'), detail=ask.get('detail', ''),
                  terminal='PASTED' in phases, terminal_attempted=True,
                  paste_intent='PASTE_INTENT' in phases,
                  native_received=ask.get('outcome') == 'CONFIRMED')
    for key in ('confirmation_scope', 'body_read_confirmed'):
        if key in ask:
            result[key] = ask[key]
    return result


def poll_locked(state, now):
    """No input: operator stop, new authenticated binding, then exact reply."""
    caller = state['caller_surface_uuid']
    if stop_file(caller).exists() and state['state'] == WAITING:
        state.update(state=STOPPED, stopped_epoch=now)
        last = state['asks'][-1]['marker'] if state['asks'] else ''
        write_channels(state, last, 'episode stopped by operator', now, status=STOPPED)
    if state['state'] != WAITING:
        return state
    binding = idle_binding(caller, state.get('workspace_uuid', '')) or {}
    if (binding.get('state') == 'BOUND' and binding.get('supervisor_uuid') == state['supervisor']
            and binding.get('task_id') and binding['task_id'] != state.get('task_id')
            and float(binding.get('last_bound_epoch', 0)) > float(state['started_epoch'])):
        state.update(state=CONSUMED, exit_reason='AUTHENTICATED_NEW_DISPATCH',
                     new_task_id=binding['task_id'], completed_epoch=now)
        last = state['asks'][-1]['marker'] if state['asks'] else ''
        write_channels(state, last, 'authenticated new dispatch', now, status=CONSUMED)
        return state
    reply = find_reply(state)
    if reply:
        state.update(state=ANSWERED, reply=dict(reply, at_epoch=now))
    if state['state'] != WAITING:
        last = state['asks'][-1]['marker'] if state['asks'] else ''
        write_channels(state, last, 'episode closed', now, status=state['state'])
    return state


def tick(caller, now=None, send=default_send):
    try:
        with episode_lock(caller):
            return tick_locked(caller, now, send)
    except BlockingIOError:
        return dict(outcome='EPISODE_BUSY', state=WAITING)


def tick_locked(caller, now=None, send=default_send):
    now = time.time() if now is None else now
    state = load(caller)
    if not state:
        return dict(outcome='NO_EPISODE')
    state['check_count'] = state.get('check_count', 0) + 1
    poll_locked(state, now)
    save(state)
    if state['state'] != WAITING:
        return state
    last = state['asks'][-1] if state['asks'] else None
    if last and now - float(last['at_epoch']) < INTERVAL:
        save(state)
        return state
    attempt = state.get('attempt_count', state['ask_count']) + 1
    # Legacy waiting episodes gain a seed without replacing any original marker.
    state.setdefault('marker_secret', secrets.token_hex(16))
    marker = issue_marker(state, attempt)
    text = build_text(state, marker, attempt)
    state['attempt_count'] = attempt
    ask = dict(marker=marker, attempt=attempt, at_epoch=now, text_sha256=ready.digest(text))
    state['asks'] = (state['asks'] + [ask])[-HISTORY:]
    if not state.get('terminal_pending'):
        state['terminal_pending'] = dict(ask, terminal=True, outcome='INTENT_UNRESOLVED')
    save(state)                      # 任何输入之前先落盘新 marker
    ask['channels'] = write_channels(state, marker, text, now)
    try:
        ask.update(send(state, marker, text))
    except Exception as exc:         # 终端失败不影响文件通道，也不重试同一轮
        ask.update(outcome='TERMINAL_ERROR', detail=f'{type(exc).__name__}: {exc}'[:400],
                   terminal=False)
    if ask.get('terminal'):
        state['terminal_sends'] = state.get('terminal_sends', 0) + 1
    if ask.get('terminal_attempted'):
        state['terminal_attempts'] = state.get('terminal_attempts', 0) + 1
    if ask.get('native_received'):
        state['native_receipts'] = state.get('native_receipts', 0) + 1
    posted = bool(ask['channels']['written'])
    state['file_posts'] = state.get('file_posts', 0) + int(posted)
    state['ask_count'] += int(posted or bool(ask.get('terminal')) or bool(ask.get('native_received')))
    pending = state.get('terminal_pending') or {}
    if pending.get('marker') == marker:
        if ask.get('native_received') or str(ask.get('outcome', '')).startswith('SKIPPED_'):
            state['terminal_pending'] = None
        else:
            state['terminal_pending'] = dict(ask, terminal=True)
    save(state)
    return state


def run(caller, max_seconds=MAX_RUN_SECONDS, poll=POLL, send=default_send,
        clock=time.time, sleep=time.sleep):
    deadline = clock() + max(1.0, min(float(max_seconds), MAX_RUN_SECONDS))
    state = tick(caller, clock(), send)
    while state.get('state') == WAITING and clock() + poll < deadline:
        sleep(poll)
        state = tick(caller, clock(), send)
    return state


def stop(caller, reason='OPERATOR'):
    with episode_lock(caller):
        ready.write_json(stop_file(caller), dict(reason=reason, at_epoch=time.time()))
        return tick_locked(caller)


def summary(state):
    if not isinstance(state, dict) or 'caller_surface_uuid' not in state:
        return state
    last = state['asks'][-1] if state.get('asks') else None
    return dict(outcome=state['state'], ask_count=state['ask_count'],
                check_count=state.get('check_count', 0), attempt_count=state.get('attempt_count', 0),
                file_posts=state.get('file_posts', 0), native_receipts=state.get('native_receipts', 0),
                terminal_attempts=state.get('terminal_attempts', 0),
                terminal_sends=state.get('terminal_sends', 0), last_ask=last,
                reply=state.get('reply'), supervisor=state['supervisor'],
                next_ask_epoch=(float(last['at_epoch']) + INTERVAL) if last else None,
                state_file=str(state_path(state['caller_surface_uuid'])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('start')
    s.add_argument('--supervisor', required=True)
    s.add_argument('--task', default='')
    s.add_argument('--transcript', default='')
    s.add_argument('--channel', action='append', default=[])
    s.add_argument('--own-prefix', action='append', default=[])
    s.add_argument('--purpose', choices=['REQUEST_NEXT_TASK', 'PENDING_TASK_REPLY'], default='REQUEST_NEXT_TASK')
    s.add_argument('--resume-stopped', action='store_true', help='explicit operator resume only')
    r = sub.add_parser('run')
    r.add_argument('--max-seconds', type=float, default=MAX_RUN_SECONDS)
    for name in ('tick', 'status', 'consume'):
        sub.add_parser(name)
    st = sub.add_parser('stop')
    st.add_argument('--reason', default='OPERATOR')
    for p in sub.choices.values():
        p.add_argument('--caller-uuid', default=os.environ.get('CMUX_SURFACE_ID', ''))
    args = parser.parse_args(argv)
    if not args.caller_uuid:
        parser.error('--caller-uuid or CMUX_SURFACE_ID required')
    caller = args.caller_uuid
    if args.cmd == 'start':
        live = ready._bridge().pin_workspace(args.supervisor, caller_uuid=caller)
        if live.get('caller_surface_uuid', '').upper() != caller.upper():
            raise ValueError('caller identity mismatch')
        state = start(caller, args.supervisor, args.task, args.transcript, args.channel,
                      caller, own_prefixes=args.own_prefix, workspace=live['workspace_uuid'],
                      purpose=args.purpose, resume_stopped=args.resume_stopped)
        state = tick(caller)
    elif args.cmd == 'run':
        state = run(caller, args.max_seconds)
    elif args.cmd == 'tick':
        state = tick(caller)
    elif args.cmd == 'stop':
        state = stop(caller, args.reason)
    elif args.cmd == 'consume':
        with episode_lock(caller):
            state = load(caller) or {}
            if state.get('state') == ANSWERED:
                state['state'] = CONSUMED
                save(state)
    else:
        state = load(caller) or dict(outcome='NO_EPISODE')
    print(json.dumps(summary(state), ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
