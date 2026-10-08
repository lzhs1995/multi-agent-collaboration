#!/usr/bin/env python3
"""PostToolUse: verify this invocation's original native delivery, read-only.

No history sweep, polling, screen-based success, or advisory/disable bypass.
Ordinary tools do not resolve identity or touch journals. An unobservable send
exits 2, with the original controller's reconciliation/recovery command.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import sys

import cmux_hook_identity
import cmux_native_delivery as native
from cmux_submission_inputs import delivery_calls
from cmux_submit_confirmation_guard import _extract_commands, is_bridge_help_command
from cmux_evidence_io import read_bytes, attempt_paths

SCRIPT_DIR = Path(__file__).resolve().parent
RECEIVED = 'NATIVE_RECEIVED'
UNVERIFIABLE = 'NATIVE_UNVERIFIABLE'


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _calls(payload):
    calls = []
    for command in _extract_commands(payload):
        if is_bridge_help_command(command):
            continue
        for call in delivery_calls(command):
            if call not in calls:
                calls.append(call)
    return calls


def _load(path):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('journal/pack must be an absolute nonsymlink file')
    raw = read_bytes(path, native.MAX_RECORD)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('journal/pack must be a JSON object')
    return value, raw


def _original(call, workspace, caller, root):
    kind = 'task' if call.get('kind') == 'text' and call.get('pack') else call.get('kind')
    pack, pack_raw, pack_path = None, None, None
    text, marker = call.get('text'), call.get('marker')
    if kind in ('task', 'callback'):
        if not call.get('pack'):
            raise ValueError('dynamic/missing task-pack path: cannot identify original attempt')
        pack_path = Path(call['pack'])
        pack, pack_raw = _load(pack_path)
        if not isinstance(pack.get('task_id'), str) or not pack['task_id']:
            raise ValueError('task pack has no task id')
        if kind == 'callback':
            text, marker = pack['completion_callback'], pack['completion_nonce']
            receipt = Path(pack['completion_receipt'])
            journal = receipt.with_name(receipt.stem + '-attempts')
        else:
            marker = marker or pack['task_id']
            journal = root / 'task-dispatch-v1' / _sha(json.dumps([caller, pack['task_id']]))
    elif kind == 'text':
        if not isinstance(marker, str) or not marker:
            raise ValueError('dynamic/missing marker: no durable original attempt identified')
        journal = root / 'message-dispatch-v1' / _sha(json.dumps([caller, marker], sort_keys=True))
    else:
        raise ValueError('unsupported raw send: use the journaled bridge')
    if not isinstance(text, str) or not text or not isinstance(marker, str) or marker not in text:
        raise ValueError('exact literal payload/marker required; cannot claim delivery of a dynamic input')
    # This single caller+marker/task slot is the invocation's controller. Never
    # search other slots, task roots, receivers or historical sessions.
    target = call.get('surface') or (pack or {}).get('callback_target')
    if not target:
        raise ValueError('dynamic/missing receiver identity')
    import cmux_bridge
    live = cmux_bridge.pin_workspace(target)
    if (str(live.get('caller_surface_uuid', '')).upper() != str(caller).upper()
            or str(live.get('workspace_uuid', '')).upper() != str(workspace).upper()):
        raise ValueError('hook identity differs from the live bridge caller')
    # The bridge's UUID spelling owns the durable key. Hook APIs may lowercase
    # UUIDs; that must not create a second slot or hide the original attempt.
    if kind in ('task', 'text'):
        slot = [live['caller_surface_uuid'], pack['task_id'] if kind == 'task' else marker]
        journal = root / ('task-dispatch-v1' if kind == 'task' else 'message-dispatch-v1') / _sha(
            json.dumps(slot, sort_keys=kind == 'text'))
    paths = attempt_paths(journal)
    if not paths:
        raise ValueError('no original attempt journal; no delivery can be confirmed')
    path = paths[-1]
    attempt, attempt_raw = _load(path)
    if attempt.get('phase') in ('PREPARED', 'NO_INPUT'):
        raise ValueError('original attempt records no input; no submission key can be recovered')
    bound = attempt['binding']
    identity = bound['identity']
    if (str(identity.get('caller_surface_uuid', '')).upper() != str(caller).upper()
            or str(identity.get('workspace_uuid', '')).upper() != str(workspace).upper()):
        raise ValueError('original attempt belongs to a different live caller/workspace')
    if any(live.get(k) != v for k, v in identity.items()):
        raise ValueError('current receiver differs from original attempt')
    report_path, report_raw = None, None
    if kind == 'callback':
        report_path = Path(pack['report'])
        if not report_path.is_absolute() or report_path.is_symlink():
            raise ValueError('callback report must be an absolute nonsymlink file')
        report_raw = read_bytes(report_path)
        if (bound.get('completion_callback') != text or bound.get('completion_nonce') != marker
                or bound.get('report_sha256') != hashlib.sha256(report_raw).hexdigest()):
            raise ValueError('callback/report differs from original attempt')
    elif bound.get('marker') != marker or bound.get('payload_sha256') != _sha(text):
        raise ValueError('payload/marker differs from original attempt')
    if pack is not None and (bound.get('task_id') != pack['task_id']
            or bound.get('task_pack_sha256') != hashlib.sha256(pack_raw).hexdigest()):
        raise ValueError('task pack differs from original attempt')
    # 核收必须沿用输入前留下的身份与 EOF fence；旧 journal 不允许追补。
    binding = attempt.get('native_binding')
    intent_at, fence = native._original_intent(attempt, binding, text)
    native.require_bound(cmux_bridge, target, binding, text, read_only=True)
    proof = native.probe(binding, text, not_before=intent_at, paste_fence=fence)
    native.require_bound(cmux_bridge, target, binding, text, read_only=True)
    if (attempt_paths(journal) != paths or read_bytes(path) != attempt_raw
            or (pack_path and read_bytes(pack_path) != pack_raw)
            or (report_path and read_bytes(report_path) != report_raw)):
        raise ValueError('original evidence changed during verification')
    command = [sys.executable, '-B', str(SCRIPT_DIR / 'cmux_bridge.py')]
    if kind == 'callback':
        command += ['submit-completion-callback', '--task-pack', str(pack_path)]
    else:
        command += ['submit-task-pack' if kind == 'task' else 'submit-text',
                    '--surface', target, '--text', text, '--marker', marker]
        if pack_path:
            command += ['--task-pack', str(pack_path)]
    result = dict(proof, attempt=str(path), kind=kind, marker=marker,
                  reconcile='rtk proxy ' + shlex.join(command + ['--reconcile-only']))
    # 只有普通消息控制器支持这个参数；提示也不能为未发送、未知或已排队的
    # 原次提供补键。真正恢复时控制器仍须复核原身份、历史和完整草稿。
    phases = [event.get('phase') for event in attempt.get('events', [])]
    if (kind == 'text' and proof.get('state') == 'NATIVE_PENDING'
            and attempt.get('phase') != 'CONFIRMED'
            and phases.count('PASTE_INTENT') == 1 and 'ENTER_SENT' in phases
            and not {'QUEUE_TAB_INTENT', 'EXTRA_ENTER_INTENT'}.intersection(phases)):
        result['recover'] = 'rtk proxy ' + shlex.join(command + ['--recover-stranded'])
    return result


def _helper_original(call, workspace, caller):
    """核同原 helper intent；只读复用同版 journal 的原生全文回执。"""
    import cmux_bridge
    import cmux_helper_evidence

    resolved = cmux_helper_evidence.resolve(call, cmux_bridge, caller, workspace)
    if not resolved:
        raise ValueError('helper invocation has no original bound targets')
    results = []
    for item in resolved:
        reconcile = 'rtk ' + shlex.join([
            'cmux-agent', 'reconcile', item['surface'], '--intent', item['helper_intent']])
        result = dict(state=UNVERIFIABLE, kind='helper', marker=item['marker'],
                      intent=item['helper_intent'], reconcile=reconcile)
        try:
            proof = cmux_helper_evidence.verify(item, cmux_bridge)
            if proof is not None:
                result.update(state=RECEIVED, helper_proof=proof)
            else:
                result['reason'] = 'original helper native receipt is not verified'
        except Exception as exc:
            result['reason'] = str(exc)
        results.append(result)
    return results


def evaluate(payload, *, state_root=None):
    calls = _calls(payload)
    if not calls:
        return dict(action='skip', results=[])
    root = Path(state_root or Path.home() / '.local/state/multi-agent-collaboration')
    results = []
    try:
        with cmux_hook_identity.evaluation(payload):
            workspace, caller = cmux_hook_identity.identity(payload)
            if not caller or workspace == 'default':
                raise ValueError('live native hook caller is unresolved')
            for call in calls:
                try:
                    if call.get('kind') == 'helper':
                        results.extend(_helper_original(call, workspace, caller))
                    else:
                        results.append(_original(call, workspace, caller, root))
                except Exception as exc:
                    results.append(dict(state=UNVERIFIABLE, reason=str(exc), call=call))
    except Exception as exc:
        results.append(dict(state=UNVERIFIABLE, reason='caller resolution: ' + str(exc)))
    return dict(action='pass' if all(r.get('state') == RECEIVED for r in results) else 'block',
                results=results)


def _render(result):
    lines = ['[multi-agent] Enter ≠ 发送。以下当前调用尚未取得接收端原生整条消息的证据：']
    for item in result['results']:
        if item['state'] == RECEIVED:
            continue
        lines.append(f"{item['state']}: {item.get('reason', '')} {item.get('attempt', '')}".rstrip())
        if item.get('reconcile'):
            lines.append('只读核收原次：' + item['reconcile'])
        if item.get('recover'):
            lines.append('原完整草稿仍在时，由控制器核身份和剩余按键预算后恢复：' + item['recover'])
    lines += ['禁止重贴、换 nonce、以按键退出码/屏幕/ACK冒充送达。',
              'queued_command 仅是待处理证据，不算已送达；只有原目标新增的完整 user 记录才可确认。']
    return '\n'.join(lines) + '\n'


def main():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
        if not isinstance(payload, dict):
            raise ValueError('hook input must be a JSON object')
        result = evaluate(payload)
    except Exception as exc:
        sys.stderr.write('[multi-agent] 原生投递核验不可用，未确认送达：' + str(exc) + '\n')
        return 2
    if result['action'] == 'block':
        sys.stderr.write(_render(result))
        return 2
    if result['action'] == 'pass':
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
