"""v2：在 v1 上把记录的查询时刻绑定到 helper 当前文件元数据；不调用Word、不签发资源归还。"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex

PROBE = Path(__file__).resolve().parent / 'pdf_pre_export_probe_v1.py'
PROBE_SHA256 = 'c294c38bd4edb049ec1fdfbc71d2efe9f5c6c4589d5a4d01f6c004caf5518a89'

def command(pdf):
    return shlex.join(['rtk', 'proxy', '/opt/homebrew/bin/python3.14', '-BE', str(PROBE),
                       '--path', os.path.realpath(pdf), '--expected-script-sha256', PROBE_SHA256])

def output_object(raw):
    value = raw
    for _ in range(6):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                # 原生 functions.exec 的两 text 块由 text_of 合并；仅剥离实测完整宿主头。
                match = re.fullmatch(r'Script completed\nWall time \d+(?:\.\d+)? seconds\nOutput:\n\s*(\{[\s\S]*\})\s*', value)
                if not match:
                    return None
                value = match.group(1)
        elif isinstance(value, dict) and value.get('schema') == 'spatial42-pdf-pre-export-filesystem-probe-v1':
            return value
        elif isinstance(value, dict) and isinstance(value.get('output'), str):
            if value.get('exit_code') != 0:
                return None
            value = value['output']
        elif isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict) and isinstance(value[0].get('text'), str):
            value = value[0]['text']
        else:
            return None
    return None

def verify_pair(call, out, pre, pdf):
    reason = 'pre-export PDF identity lacks a paired immutable filesystem-query call'
    if call is None or out is None:
        return False, reason
    if not (call.get('type') == 'custom_tool_call' and call.get('name') == 'exec' and call.get('ns') in (None, 'functions')):
        return False, reason
    if out.get('type') != 'custom_tool_call_output':
        return False, reason + ' (wrong output record type)'
    if not (call.get('call_id') and call['call_id'] == out.get('call_id') and
            call.get('src') == out.get('src') and call.get('off', -1) < out.get('off', -1) and
            call.get('t', float('inf')) <= out.get('t', float('-inf'))):
        return False, reason + ' (call/output mismatch)'
    match = re.fullmatch(r'\s*text\s*\(\s*await\s+tools\.exec_command\s*\((\{[\s\S]*\})\)\s*\)\s*;?\s*', call.get('code', ''))
    if not match:
        return False, reason + ' (unsupported host wrapper)'
    try:
        options = json.loads(match.group(1))
    except (ValueError, TypeError):
        return False, reason + ' (host options not literal JSON)'
    if not isinstance(options, dict) or not set(options).issubset({'cmd', 'yield_time_ms', 'max_output_tokens'}):
        return False, reason + ' (unknown host option)'
    if any(type(options[k]) is not int for k in ('yield_time_ms', 'max_output_tokens') if k in options):
        return False, reason + ' (invalid host option type)'
    if options.get('cmd') != command(pdf):
        return False, reason + ' (command does not query the exact PDF with the fixed helper)'
    # v2 P1-D4-HELPER-TIME：记录的查询时刻须晚于 helper 文件当前的 ctime/mtime/birthtime，且核 SHA 前后文件元数据不变。
    # ctime 只是文件系统元数据，不是密码学不可变；它只证明当前这份 helper 在查询之前就已是现在的样子。
    qt = [call.get('t'), out.get('t')]
    if not all(type(v) in (int, float) and math.isfinite(v) for v in qt):
        return False, reason + ' (query time missing or not finite)'
    before = os.stat(PROBE)
    if hashlib.sha256(PROBE.read_bytes()).hexdigest() != PROBE_SHA256:
        return False, reason + ' (helper bytes changed)'
    after = os.stat(PROBE)
    ident = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if ident(before) != ident(after):
        return False, reason + ' (helper changed during verification)'
    if max(after.st_ctime, after.st_mtime, getattr(after, 'st_birthtime', 0)) >= min(qt):
        return False, reason + ' (helper created or changed at/after the recorded query)'
    data = output_object(out.get('out'))
    if not data or data.get('status') != 'READY' or data.get('read_only') is not True or data.get('helper_sha256') != PROBE_SHA256:
        return False, reason + ' (query output not verified)'
    if data.get('path') != os.path.realpath(pdf) or type(data.get('existed')) is not bool or data['existed'] is not pre.get('existed'):
        return False, reason + ' (path/existence mismatch)'
    if data['existed']:
        row = data.get('file')
        if not isinstance(row, dict) or row.get('sha256') != pre.get('sha256') or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256'))) or not isinstance(row.get('identity'), list) or len(row['identity']) != 5:
            return False, reason + ' (prior file identity/SHA mismatch)'
    elif data.get('file') is not None or data.get('absence') != 'FileNotFoundError':
        return False, reason + ' (absence not established)'
    return True, None
