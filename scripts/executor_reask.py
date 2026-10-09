#!/usr/bin/env python3
"""空闲 executor 每 60 秒主动求派，直到 Codex 主管答复（用户 2026-10-09 明令）。

每一轮都是带新 marker 的新请求，从不重贴旧消息：
- 文件通道：每轮覆盖写 <channel>/EXECUTOR_REASK_<caller8>_CURRENT.json（主管轮内也能读）；
- 终端通道：仅当主管 composer 为空、且本段没有仍排队/卡在 compose 的旧请求时，
  才经 executor_ready 的 journaled bridge 发送一次；否则只走文件通道，绝不堵 composer。
答复 = 本人 transcript 新增的真实 user turn、mailbox 回复，或通道里引用本段 marker
的主管文件。只有答复、新派发或 operator stop 结束一段；配套 Stop hook
（cmux_executor_reask_stop_guard.py）在段内拦住结束，要求继续 run。
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time

import executor_ready as ready

INTERVAL = 60.0
POLL = 5.0
MAX_RUN_SECONDS = 540.0
MAX_TEXT = 700          # 实测 1204/1518 字被 Codex 折成 [Pasted Content N chars]，bridge 无法认领
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


def current_name(caller):
    return f'EXECUTOR_REASK_{caller[:8]}_CURRENT.json'


def configured_channels(supervisor):
    conf = ready.read_json(config_path()) or {}
    value = (conf.get('channels') or {}).get(supervisor, [])
    return [str(v) for v in value if isinstance(v, str)] if isinstance(value, list) else []


def start(caller, supervisor, task_id='', transcript='', channels=None, executor_ref='',
          now=None, reason='MANUAL', own_prefixes=()):
    """开一段新的求派；已在等待的同一主管段原样保留（不重置计数/期限）。"""
    now = time.time() if now is None else now
    old = load(caller)
    if old and old.get('state') == WAITING and old.get('supervisor') == supervisor:
        return old
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
    state = dict(version=1, caller_surface_uuid=caller, executor_ref=executor_ref or caller,
                 supervisor=supervisor, task_id=task_id, state=WAITING, reason=reason,
                 started_epoch=now, episode_id=ready.digest(json.dumps([caller, supervisor, now])),
                 transcript=transcript, tail_position=tail, channels=chans,
                 mailbox=str(ready.mailbox_dir(caller)), ask_count=0, asks=[],
                 terminal_sends=0, reply=None, own_prefixes=list(own_prefixes))
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


def find_reply(state):
    """主管答复的三种来源；本人写的 CURRENT 和本人文件不算。"""
    caller, since = state['caller_surface_uuid'], float(state['started_epoch'])
    markers = [a['marker'] for a in state.get('asks', []) if isinstance(a, dict) and a.get('marker')]
    for path in _recent_files(state['mailbox'], since):
        body = ready.read_json(path)
        if (body and body.get('status') in ready.REPLY_STATES and body.get('queued') is not True
                and (body.get('marker') in markers or body.get('caller_surface_uuid') == caller)):
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
            hit = next((m for m in markers if m in text), None)
            if hit:
                return dict(source='channel', path=str(path), marker=hit, text=text[:800])
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
            if texts:
                state['tail_position'] = tail.position()
                return dict(source='transcript', text=texts[0][:800])
        state['tail_position'] = tail.position()
    return None


def build_text(state, marker, attempt):
    mailbox = state['mailbox']
    text = (f"{ready.PREFIX}|{state['executor_ref']}|{marker} ask #{attempt}"
            f" (every {int(INTERVAL)}s until you reply): executor "
            f"{state['caller_surface_uuid'][:8]} is IDLE"
            + (f" after {state['task_id']}" if state.get('task_id') else '')
            + ". Please dispatch the next task pack now, or reply STATUS: WAITING_DEPENDENCY "
            f"/ SOLO with its trigger. File reply: {mailbox}/{marker}.json "
            + json.dumps(dict(marker=marker, caller_surface_uuid=state['caller_surface_uuid'],
                              status='TASK|WAITING_DEPENDENCY|SOLO'), ensure_ascii=False))
    if len(text) > MAX_TEXT:
        raise ValueError(f'ASK_TOO_LONG: {len(text)} > {MAX_TEXT}')
    return text


def write_channels(state, marker, text, now, status=WAITING):
    written = []
    for channel in state.get('channels', []):
        path = Path(channel) / current_name(state['caller_surface_uuid'])
        try:
            ready.write_json(path, dict(
                schema='executor-reask-current-v1', status=status, at_epoch=now,
                at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                caller_surface_uuid=state['caller_surface_uuid'], supervisor=state['supervisor'],
                task_id=state.get('task_id', ''), ask_count=state['ask_count'], marker=marker,
                request=text, reply_mailbox=f"{state['mailbox']}/{marker}.json",
                terminal_inputs_this_round=0 if status != WAITING else None))
            written.append(str(path))
        except OSError as exc:
            written.append(f'ERROR {path}: {exc}')
    return written


def _unreceived_on_screen(bridge, supervisor, caller, ask, flat_screen):
    """已发终端请求无原生回执、但 marker 仍在屏上 = 仍待投递（排队/compose）。

    不依赖 bridge 的 queue banner 正则：2026-10-09 实测本版 Codex 横幅为
    "Queued follow-up inputs"，_PENDING_QUEUE_RE 不匹配，#9 排队时 #10 仍被贴出。
    已原生收到的请求留在历史区也会显示 marker，所以先查回执再看屏幕。
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
            return 'QUEUED', screen
    if not bridge.compose_block_is_empty(screen):
        return 'COMPOSE', screen
    if bridge.receiver_cannot_submit_now(screen):
        return 'BUSY', screen
    return None, screen


def default_send(state, marker, text):
    """一次 journaled 发送；结果按 executor_ready 原样保留，不重试。"""
    bridge = ready._bridge()
    held, _screen = _held(bridge, state['supervisor'], state.get('asks', []),
                          state['caller_surface_uuid'])
    if held:
        return dict(outcome='SKIPPED_' + held, terminal=False)
    ready.write_record(state['caller_surface_uuid'], marker, text, state['supervisor'],
                       state.get('task_id', ''), state['episode_id'])
    ask = dict(marker=marker)
    ready.advance_ask(bridge, state['supervisor'], state['caller_surface_uuid'], ask,
                      allow_send=True)
    return dict(outcome=ask.get('outcome'), detail=ask.get('detail', ''), terminal=True)


def tick(caller, now=None, send=default_send):
    now = time.time() if now is None else now
    state = load(caller)
    if not state:
        return dict(outcome='NO_EPISODE')
    if ready.read_json(stop_file(caller)) is not None and state['state'] == WAITING:
        state.update(state=STOPPED, stopped_epoch=now)
        save(state)
    if state['state'] != WAITING:
        return state
    reply = find_reply(state)
    if reply:
        state.update(state=ANSWERED, reply=dict(reply, at_epoch=now))
        last = state['asks'][-1]['marker'] if state['asks'] else ''
        write_channels(state, last, 'answered', now, status=ANSWERED)
        save(state)
        return state
    last = state['asks'][-1] if state['asks'] else None
    if last and now - float(last['at_epoch']) < INTERVAL:
        save(state)
        return state
    attempt = state['ask_count'] + 1
    marker = f'{ready.PREFIX}_{secrets.token_hex(8)}'
    text = build_text(state, marker, attempt)
    state['ask_count'] = attempt
    ask = dict(marker=marker, attempt=attempt, at_epoch=now, text_sha256=ready.digest(text))
    state['asks'] = (state['asks'] + [ask])[-HISTORY:]
    save(state)                      # 任何输入之前先落盘新 marker
    ask['channels'] = write_channels(state, marker, text, now)
    try:
        ask.update(send(state, marker, text))
    except Exception as exc:         # 终端失败不影响文件通道，也不重试同一轮
        ask.update(outcome='TERMINAL_ERROR', detail=f'{type(exc).__name__}: {exc}'[:400],
                   terminal=False)
    if ask.get('terminal'):
        state['terminal_sends'] = state.get('terminal_sends', 0) + 1
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
    ready.write_json(stop_file(caller), dict(reason=reason, at_epoch=time.time()))
    return tick(caller)


def summary(state):
    if not isinstance(state, dict) or 'caller_surface_uuid' not in state:
        return state
    last = state['asks'][-1] if state.get('asks') else None
    return dict(outcome=state['state'], ask_count=state['ask_count'],
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
        state = start(caller, args.supervisor, args.task, args.transcript, args.channel,
                      os.environ.get('CMUX_SURFACE_ID', ''), own_prefixes=args.own_prefix)
        state = tick(caller)
    elif args.cmd == 'run':
        state = run(caller, args.max_seconds)
    elif args.cmd == 'tick':
        state = tick(caller)
    elif args.cmd == 'stop':
        state = stop(caller, args.reason)
    elif args.cmd == 'consume':
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
