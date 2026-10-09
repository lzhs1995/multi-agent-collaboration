#!/usr/bin/env python3
"""Stop hook：空闲 executor 在主管答复前不得结束，必须继续 60 秒一轮的主动求派。

- 求派段 WAITING_REPLY：一律拦 Stop（含 stop_hook_active 重入），要求运行
  `executor_reask.py run`；只有主管答复、新派发或 operator stop 放行。
- 段内已收到答复 ANSWERED：拦一次，要求按答复执行，随后记 CONSUMED 放行。
- 无段但 idle binding 显示本 executor 刚空闲（原任务 marker 已 disarm）：自动开段并拦。
hook 自身只读写状态文件，不读屏、不发送任何输入。
"""
import json
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor_ready as ready  # noqa: E402
import executor_reask as reask  # noqa: E402

SELF = Path(__file__).resolve().parent / 'executor_reask.py'
PY = sys.executable or 'python3'
IDLE_STATES = ('IDLE_WAITING_DEPENDENCY', 'IDLE_ASKING')


def _run_cmd(caller):
    return (f'{shlex.quote(PY)} -B {shlex.quote(str(SELF))} run '
            f'--caller-uuid {shlex.quote(caller)}')


def _idle_binding(caller):
    """本 executor 的 idle binding（由 cmux_executor_idle_guard 写）。"""
    bindings = ready.idle_root() / 'bindings'
    try:
        names = sorted(os.listdir(bindings))[:256]
    except OSError:
        return None
    for name in names:
        body = ready.read_json(bindings / name)
        if body and body.get('surface_uuid') == caller:
            return body
    return None


def decide(payload, caller, now=None):
    """返回 (block: bool, reason: str)。"""
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'Stop' or not caller:
        return False, ''
    state = reask.load(caller)
    transcript = payload.get('transcript_path') or ''
    if state and state.get('state') == reask.WAITING:
        if transcript and not state.get('transcript'):
            try:
                state.update(transcript=transcript, tail_position=ready.Tail(transcript).position())
                reask.save(state)
            except (OSError, ValueError):
                pass
        # 只核答复，不消耗求派轮次；发送只在 run 里做。
        if reask.ready.read_json(reask.stop_file(caller)) is not None:
            state.update(state=reask.STOPPED, stopped_epoch=now)
        else:
            reply = reask.find_reply(state)
            if reply:
                state.update(state=reask.ANSWERED, reply=dict(reply, at_epoch=now))
        reask.save(state)
    if state and state.get('state') == reask.ANSWERED:
        reply = state.get('reply') or {}
        state['state'] = reask.CONSUMED
        reask.save(state)
        return True, ('[executor-reask] 主管已答复（来源 %s%s）。立刻按答复执行：派了任务就开工，'
                      'WAITING_DEPENDENCY/SOLO 就按其触发条件推进，不要停在空等。\n答复摘录：%s'
                      % (reply.get('source', '?'),
                         (' ' + reply['path']) if reply.get('path') else '',
                         str(reply.get('text', ''))[:600]))
    if not state or state.get('state') in (reask.CONSUMED, reask.STOPPED):
        binding = _idle_binding(caller)
        started = float(state.get('started_epoch', 0)) if state else 0.0
        if (binding and binding.get('state') in IDLE_STATES and binding.get('supervisor_uuid')
                and float(binding.get('idle_since_epoch') or 0) > started):
            state = reask.start(caller, binding['supervisor_uuid'], binding.get('task_id', ''),
                                transcript, executor_ref=caller, now=now, reason='AUTO_IDLE')
        else:
            return False, ''
    if state.get('state') != reask.WAITING:
        return False, ''
    last = state['asks'][-1] if state.get('asks') else {}
    return True, (
        '[executor-reask] 你是空闲 executor，Codex 主管(%s)尚未答复；禁止停下或空等。'
        '已求派 %d 次，上次 %s（%s）。立刻运行：\n%s\n'
        '它每 %d 秒发一条新 marker 的求派（文件通道每轮必写；主管 composer 空闲才走终端），'
        '直到主管答复/新派发；返回仍 WAITING_REPLY 就再运行一次。只有 operator 可用 '
        '`executor_reask.py stop` 结束。' % (
            state['supervisor'][:8], state.get('ask_count', 0),
            time.strftime('%H:%M:%SZ', time.gmtime(float(last['at_epoch']))) if last else '尚未',
            last.get('outcome', '-'), _run_cmd(caller), int(reask.INTERVAL)))


def main():
    try:
        payload = json.loads(sys.stdin.read(ready.MAX_JSON_BYTES + 1) or '{}')
    except (ValueError, OSError):
        return 0
    caller = os.environ.get('CMUX_SURFACE_ID', '') or (payload.get('surface_uuid', '')
                                                        if isinstance(payload, dict) else '')
    try:
        block, reason = decide(payload, caller)
    except Exception as exc:  # 状态损坏时不制造无提示死循环，但要在 stderr 留证
        print(f'[executor-reask] INTERNAL_ERROR {type(exc).__name__}: {exc}', file=sys.stderr)
        return 0
    if block:
        print(json.dumps(dict(decision='block', reason=reason), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
