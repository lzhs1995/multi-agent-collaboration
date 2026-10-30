# 私有验收器 v10（c4545 V46，基于冻结 v9；仅把配对查询改用 paired_pre_export_probe_v2 的查询时刻绑定；其他边界不变）
# 自声明 role、只读 getState/getApp、跨会话/合成来源都不能 VERIFIED；--fixture-root 只供逻辑测试，最高 FIXTURE_ONLY
import json, hashlib, os, sys, re, shutil, subprocess, datetime, urllib.parse, argparse
from paired_pre_export_probe_v2 import verify_pair as verify_pre_probe_pair
ROLES = ('save', 'close', 'reopen', 'export')
READS = ('read', 'before', 'after_close')  # 只读角色：保存前原生读取、关闭后窗口状态
CALLS, OUTS = ('function_call', 'custom_tool_call'), ('function_call_output', 'custom_tool_call_output')
ERR = re.compile(r'timeoutReached|-1000\d|\bError:|Exception:|denied')
SAVED = re.compile(r'已儲存|已保存|Saved')
PAGES = re.compile(r'共\s*(\d+)\s*[頁页]|Page\s+\d+\s+of\s+(\d+)')
WINDOW = re.compile(r'Window:|標準視窗|App: Microsoft Word')
# 只读调用与 JS 内建方法先剔除，剩下仍有方法调用才算动作
READONLY = re.compile(r'\b\w+\s*\.\s*(?:get|list|find|read|query|describe|snapshot|screenshot|wait|emit)\w*\s*\([^()]*\)'
                      r'|\b(?:JSON|Promise|Object|Array|Math|console|String|Number|Date)\s*\.\s*\w+\s*\('
                      r'|\.\s*(?:map|filter|join|then|catch|forEach|reduce|slice|includes|indexOf|push|concat|some|every|sort|split|trim|replace|toString|toFixed|startsWith|endsWith|substring|keys|values|entries)\s*\(|\btext\s*\(')
ACTION = re.compile(r'\.\s*[A-Za-z_]\w*\s*\(')
SESSIONS = os.path.realpath('/Users/lzhs/.codex/sessions')
IGNORED = ('process_alive', 'doctor', 'install', 'pass')
def ts(x): return datetime.datetime.fromisoformat(x.replace('Z', '+00:00')).timestamp()
def text_of(o):
    # F5：字符串输出先按 JSON 解码，list/dict 取 text 项
    if isinstance(o, str):
        try: o = json.loads(o)
        except ValueError: return o
    if isinstance(o, dict): o = [o]
    # v7 E1：shell 输出块可能只有 output 字符串键（无 text）
    return ' '.join(i['text'] if isinstance(i.get('text'), str) else i['output'] for i in o if isinstance(i, dict) and (isinstance(i.get('text'), str) or isinstance(i.get('output'), str))) if isinstance(o, list) else str(o or '')
def native(rec, actor, fixture_root):
    # 来源须是原 actor 的 Codex 转录（路径、文件名、首行 session_meta 三者一致），再按偏移读整行字节核 SHA
    src = os.path.realpath(rec['source'])
    if fixture_root:
        if not src.startswith(os.path.realpath(fixture_root) + os.sep): raise ValueError('fixture source outside --fixture-root')
    elif not (src.startswith(SESSIONS + os.sep) and os.path.basename(src).endswith(actor + '.jsonl')):
        raise ValueError('source is not the original actor rollout: %s' % src)
    with open(src, 'rb') as f:
        meta = json.loads(f.readline())
        if not fixture_root and (meta.get('type') != 'session_meta' or meta.get('payload', {}).get('id') != actor):
            raise ValueError('rollout session_meta id != actor session')
        f.seek(max(rec['offset'] - 1, 0)); prev = f.read(1) if rec['offset'] else b'\n'
        f.seek(rec['offset']); b = f.read(rec['bytes'])
    if prev != b'\n' or not b.endswith(b'\n') or len(b) != rec['bytes'] or hashlib.sha256(b).hexdigest() != rec['sha256']:
        raise ValueError('native bytes are not one exact line at %s:%s' % (src, rec['offset']))
    x = json.loads(b); p = x.get('payload', {}); a = p.get('arguments') or p.get('input') or ''
    try: code = json.loads(a).get('code', '') if isinstance(a, str) else ''
    except (ValueError, AttributeError): code = a if isinstance(a, str) else ''
    return dict(role=rec.get('role'), t=ts(x['timestamp']), type=p.get('type', ''), call_id=p.get('call_id') or '', ns=p.get('namespace'),
                name=p.get('name'), code=code, out=text_of(p.get('output')), src=src, off=rec['offset'], nb=rec['bytes'])
def pair(rs, role):
    # F4：角色可为有序多步；每步恰好一条调用+一条输出、同一非空 call_id、同一来源，步与步不交错
    mine = [r for r in rs if r['role'] == role]
    if not mine: return None, '%s needs exactly one native call and one output record' % role
    steps, out = {}, []
    for r in mine: steps.setdefault(r['call_id'], []).append(r)
    for cid, rr in steps.items():
        calls = [r for r in rr if r['type'] in CALLS]; outs = [r for r in rr if r['type'] in OUTS]
        if len(calls) != 1 or len(outs) != 1 or len(rr) != 2: return None, '%s needs exactly one native call and one output record per step' % role
        c, o = calls[0], outs[0]
        if not cid or o['t'] < c['t'] or c['src'] != o['src'] or o['off'] < c['off']: return None, '%s call/output call_id, order or source mismatch' % role
        if (c['ns'], c['name']) != ('mcp__cua_repl', 'js'): return None, '%s call is not mcp__cua_repl js' % role
        if ERR.search(o['out']): return None, '%s output carries an error marker' % role
        out.append((c, o))
    out.sort(key=lambda x: x[0]['off'])
    if any(b[0]['off'] < a[1]['off'] for a, b in zip(out, out[1:])): return None, '%s chain steps interleave' % role
    if role in READS and len(out) != 1: return None, '%s needs exactly one native call and one output record' % role
    return out, None
# v4 动作核验：先去掉 JS 注释与字符串，再按有限语法逐句解析；未知语法、未绑定 Word 的对象、文档外方法一律拒绝
APP_METHODS = {'pressKey', 'paste', 'typeText', 'click', 'drag', 'scroll', 'selectText', 'setValue', 'performSecondaryAction', 'getAXState', 'getScreenshot', 'getAXStateAndScreenshot'}
APP_READ = {'getAXState', 'getScreenshot', 'getAXStateAndScreenshot'}
ROLE_KEYS = dict(save='super+s', close='super+w')
ALIAS = {'cmd': 'super', 'command': 'super', 'meta': 'super', 'control': 'ctrl', 'option': 'alt', 'opt': 'alt'}
LABELS = dict(save=re.compile(r'^(?!.*(?:另存|Save As)).*(?:儲存|保存|Save)'), close=re.compile(r'關閉|关闭|Close'), export=re.compile(r'匯出|导出|Export|PDF'))
WORD_APPS = ('Microsoft Word', 'com.microsoft.Word')
BIND = re.compile(r'(?:(?:let|const|var)\s+)?([A-Za-z_$][\w$]*)\s*=\s*await\s+cua\s*\.\s*getApp\s*\(\s*§(\d+)§\s*\)')
CUA_READ = re.compile(r'(?:await\s+)?cua\s*\.\s*(?:getState|getApp|listApps)\s*\(\s*(?:§\d+§)?\s*\)')
CALL = re.compile(r'(?:await\s+)?([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_]\w*)\s*\(\s*((?:§\d+§|\d+|\[\s*\d+\s*,\s*\d+\s*\]|\{[^{}()§]*\}|\{\s*(?:index|element|id)\s*:\s*(?:§\d+§|\d+)\s*\})(?:\s*,\s*(?:§\d+§|\{[^{}()]*\}))?)?\s*\)')
INFO = {}
def lex(code):
    out, strs, i, n = [], [], 0, len(code)
    while i < n:
        if code.startswith('//', i): j = code.find('\n', i); i = n if j < 0 else j; continue
        if code.startswith('/*', i):
            j = code.find('*/', i + 2)
            if j < 0: return None, None
            out.append(' '); i = j + 2; continue
        c = code[i]
        if c in '"\'`':
            j, buf = i + 1, []
            while j < n and code[j] != c:
                if c == '`' and code.startswith('${', j): return None, None
                buf.append(code[j:j + 2] if code[j] == '\\' else code[j]); j += 2 if code[j] == '\\' else 1
            if j >= n: return None, None
            strs.append(''.join(buf)); out.append('§%d§' % (len(strs) - 1)); i = j + 1; continue
        out.append(c); i += 1
    return ''.join(out), strs
def keynorm(k):
    p = [ALIAS.get(x.strip().lower(), x.strip().lower()) for x in k.split('+')]
    return '+'.join(sorted(p[:-1]) + p[-1:])
def analyze(code, role, binds, prior, base, mark=None):
    # 返回 ('OK'|'REFUSE'|'PENDING', 理由)；按键须是本角色键，点击须绑定上一条原生输出里的控件标签
    lexed, strs = lex(code)
    if lexed is None: return 'REFUSE', '%s call code has unterminated or interpolated literals' % role
    lines, proven, pend, dyn = dict(re.findall(r'(?m)^\s*(\d+)\s+(.+)$', prior)), False, None, 0
    for st in [x.strip() for x in re.split(r'[;\n]', lexed) if x.strip()]:
        m = BIND.fullmatch(st)
        if m: binds[m.group(1)] = strs[int(m.group(2))]; continue
        if CUA_READ.fullmatch(st): continue
        m = CALL.fullmatch(st)
        if not m: return 'REFUSE', '%s call has unparseable statement %r' % (role, st[:60])
        var, meth, arg = m.groups()
        if binds.get(var) not in WORD_APPS: return 'REFUSE', '%s calls %s.%s on an object not bound to Microsoft Word' % (role, var, meth)
        if meth not in APP_METHODS: return 'REFUSE', '%s uses unsupported App method %s' % (role, meth)
        if meth in APP_READ: continue
        if role in READS: return 'REFUSE', '%s read performs an action (%s)' % (role, meth)
        a0 = (arg or '').lstrip()
        if meth in ('setValue', 'performSecondaryAction') and not re.fullmatch(r'\d+\s*,\s*§\d+§', a0): return 'REFUSE', '%s %s needs (elementIndex, string)' % (role, meth)
        mm = None if a0.startswith('[') or (a0.startswith('{') and not re.match(r'\{\s*(?:index|element|id)\s*:', a0)) else re.search(r'§(\d+)§|(\d+)', a0)
        ref = (strs[int(mm.group(1))] if mm.group(1) else mm.group(2)) if mm else None
        if meth == 'pressKey':
            k = keynorm(ref or ''); other = [r for r, rk in ROLE_KEYS.items() if rk == k and r != role]
            if other: return 'REFUSE', '%s pressKey %s belongs to %s' % (role, k, other[0])
            proven = proven or ROLE_KEYS.get(role) == k; continue
        dyn += 1; label = lines.get(ref) if ref and ref.isdigit() else (ref if ref and ref in prior else None)
        if dyn > 1 or label is None: pend = '%s %s step is not bound to a control in the prior native output' % (role, meth); continue
        if meth not in ('click', 'performSecondaryAction'): continue  # 输入类步骤只需绑定控件，不证明也不冲突
        hits = [r for r, rx in LABELS.items() if rx.search(label)] + (['reopen'] if base and base in label else [])
        bad = [r for r in hits if r != role and not (role == 'export' and r == 'save')]
        if bad: return 'REFUSE', '%s %s targets %r, a %s control' % (role, meth, label[:40], bad[0])
        proven = proven or role in hits
    if role in READS: return 'OK', None
    if mark is not None:
        mark['proven'] = mark.get('proven') or proven; return ('PENDING', pend) if pend else ('OK', None)
    if not proven: return ('PENDING', pend) if pend else ('REFUSE', '%s call has no %s action (save=super+s, close=super+w; reopen/export need a bound control)' % (role, role))
    return ('PENDING', pend) if pend else ('OK', None)
def pages(pdf):
    tool = shutil.which('pdfinfo') or '/opt/homebrew/bin/pdfinfo'
    if os.path.exists(tool):
        m = re.search(r'^Pages:\s+(\d+)', subprocess.run([tool, pdf], capture_output=True, text=True, timeout=20).stdout, re.M)
        if m: return int(m.group(1))
    return len(re.findall(rb'/Type\s*/Page(?![s\w])', open(pdf, 'rb').read()))
def in_win(path, lo, hi): return lo - 2 <= os.path.getmtime(path) <= hi + 2
# v6 CR3：完整路径边界匹配（JS 字面量与 file:// URL 先真实解码），前缀/后缀路径不算点名
def jsdec(s):
    try: return json.loads('"' + s.replace('"', '\\"').replace("\\'", "'") + '"')
    except ValueError: return s
FILEURL = re.compile(r'file://[^\s"\',]+')
def names(text, path):
    for v in {path, os.path.realpath(path)}:
        if re.search(r'(?<![\w.\-~/])' + re.escape(v) + r'(?![\w.\-~#/])', text): return True
    return os.path.realpath(path) in urls(text)  # v7 D1：file:// URL 解码后与实路径全等才算点名；.bak/前缀不等
def urls(text): return {os.path.realpath(urllib.parse.unquote(urllib.parse.urlparse(u).path)) for u in FILEURL.findall(text)}
# v6 CR2：原稿 URL 绑定须解码后等于原稿实路径
def bound(out, doc): return any(os.path.realpath(urllib.parse.unquote(urllib.parse.urlparse(u).path)) == doc for u in re.findall(r'URL:\s*(file://[^\s"\',]+)', out))
DOCPATH = re.compile(r'(?<![\w/~])~?/(?:[^\s"\'<>|:/]+/)+[^\s"\'<>|:/]+\.(?:docx?|pdf|rtf|pages)(?![\w.\-~])', re.I)  # v7 D2：~/ 整段取出再 expanduser
def logpayload(line):
    try: row = json.loads(line)
    except (ValueError, TypeError): return None
    if not isinstance(row, dict): return None
    payload = row.get('payload')
    return payload if isinstance(payload, dict) else None
def gapstate(b):
    # v8 R1：缺失或非对象 payload 不能证明重开后的文稿绑定仍新鲜。
    for line in b.splitlines():
        if not line.strip(): continue
        payload = logpayload(line)
        if payload is None: return 'UNKNOWN'
        if payload.get('type') in CALLS: return 'CALL'
    return 'CLEAN'
def verify(ev, actor, fixture_root=None):
    rs = [native(r, actor, fixture_root) for r in ev.get('native_records', [])]
    url = ev.get('document', {}).get('url', ''); reads = []
    if any(r['role'] == 'read' for r in rs):
        got, why = pair(rs, 'read')
        if got and url and ('URL: ' + url) in got[0][1]['out']: reads.append('NATIVE_READ_OK')
    if not any(r['role'] in ROLES for r in rs):
        return ('NATIVE_READ_OK_ROUNDTRIP_PENDING' if reads else 'NO_VERIFIED_NATIVE_READ'), reads, ['no save/close/reopen/export native records']
    unknown = sorted({str(r['role']) for r in rs} - set(ROLES + READS))
    got, fails = {}, (['unknown role labels %s' % unknown] if unknown else [])
    # 保存前须有一次原生读取；关闭后可选一次只读状态读取
    order = ['before'] + list(ROLES[:2]) + (['after_close'] if any(r['role'] == 'after_close' for r in rs) else []) + list(ROLES[2:])
    for role in order:
        got[role], why = pair(rs, role)
        if why: fails.append(why)
    if fails: return 'FAIL', reads, fails
    binds, pend, prev = {}, [], None; base = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path))
    for role in order:
        mark, rp = {}, []
        for c, o in got[role]:
            st, why = analyze(c['code'], role, binds, prev or '', base, mark); prev = o['out']
            if st == 'REFUSE': fails.append(why)
            elif st == 'PENDING': rp.append(why)
        if role not in READS and not mark.get('proven') and not rp: fails.append('%s call has no %s action (save=super+s, close=super+w; reopen/export need a bound control)' % (role, role))
        pend.extend(rp)
    if fails: return 'FAIL', reads, fails
    al = [x for role in order for x in got[role]]; seq = [t for c, o in al for t in (c['t'], o['t'])]; offs = [v for c, o in al for v in (c['off'], o['off'])]
    if seq != sorted(seq) or offs != sorted(offs) or len({c['call_id'] for c, o in al}) != len(al) or len({c['src'] for c, o in al}) != 1:
        fails.append('roles not strictly before<save<close<reopen<export with distinct call_ids in one rollout')
    d, p = ev['document'], ev['pdf']
    if os.path.realpath(urllib.parse.unquote(urllib.parse.urlparse(url).path)) != os.path.realpath(d['path']): fails.append('document URL != document path')
    if hashlib.sha256(open(d['path'], 'rb').read()).hexdigest() != d['post_save_sha256']: fails.append('document SHA != declared post-save SHA')
    sc, co, ro = got['save'][0][0], got['close'][-1][1], got['reopen'][-1][1]; ec, eo = got['export'][0][0], got['export'][-1][1]
    # 效果核验：保存须在调用窗内真写盘；关闭后输出不再含原稿；重开输出须有原 URL、已保存标签与原生页数
    # 已保存稿 no-op 不写盘：动作证明与写盘证明分开报告，不为通过而改稿
    if in_win(d['path'], sc['t'], co['t']): INFO['save_write'] = 'WRITTEN_IN_WINDOW'
    elif d.get('pre_save_sha256') == d['post_save_sha256'] and SAVED.search(got['before'][0][1]['out']): INFO['save_write'] = 'NO_OP_ALREADY_SAVED'
    else: fails.append('document mtime not inside save call..close output window (no observable save write)')
    if ('URL: ' + url) not in got['before'][0][1]['out']: fails.append('pre-save native read lacks exact document URL line')
    gone = got['after_close'][0][1]['out'] if 'after_close' in got else co['out']
    if url in gone or not WINDOW.search(gone): fails.append('no native window state showing the document window gone after close')
    if ('URL: ' + url) not in ro['out'] or not SAVED.search(ro['out']): fails.append('reopen output lacks exact URL line or saved label')
    m = PAGES.search(ro['out']); n = int(m.group(1) or m.group(2)) if m else None
    if n is None: fails.append('reopen output has no native page count')
    pb = open(p['path'], 'rb').read()
    if hashlib.sha256(pb).hexdigest() != p['sha256'] or not pb.startswith(b'%PDF-'): fails.append('PDF SHA/header mismatch')
    if not in_win(p['path'], ec['t'], eo['t'] + 120): fails.append('PDF mtime not inside export call..output+120s window')
    # F1/CR3：PDF 实路径须由导出链完整点名（解码后的字符串字面量或原生输出，边界匹配）
    real, doc = os.path.realpath(p['path']), os.path.realpath(d['path'])
    chain = '\n'.join('\n'.join(jsdec(s) for s in (lex(c['code'])[1] or [])) + '\n' + o['out'] for c, o in got['export'])
    if not names(chain, p['path']): pend.append('export chain never names the PDF path')
    # F2/CR2：导出链输出出现别的文稿 URL/完整路径即 FAIL；须有新鲜原稿 URL 绑定（链内，或重开输出且其后到导出前无链外调用）
    outs = '\n'.join(o['out'] for c, o in got['export'])
    foreign = sorted((urls(outs) | {os.path.realpath(os.path.expanduser(x)) for x in DOCPATH.findall(outs)}) - {doc, real})
    if foreign: fails.append('export chain output shows another document %s' % foreign[0])
    fresh_url = any(bound(o['out'], doc) for c, o in got['export'])
    if not fresh_url and bound(ro['out'], doc):
        with open(ec['src'], 'rb') as f: f.seek(ro['off'] + ro['nb']); gap = f.read(max(ec['off'] - ro['off'] - ro['nb'], 0))
        g = gapstate(gap); fresh_url = g == 'CLEAN'
        if g == 'UNKNOWN': pend.append('unparseable record between the reopen output and the first export call; freshness unknown')
    if not fresh_url: pend.append('no fresh native AX output binding the original document URL before or within the export chain')
    # F3/CR1：导出前 PDF 身份须指到原 actor 导出前的一条原生输出记录（offset/bytes/SHA）；自报 dict、birthtime/mtime/SHA 单独不充分
    pre, born = p.get('pre_export'), os.stat(real).st_birthtime; INFO['pdf_identity'] = 'UNKNOWN'
    pr = native(pre['native_record'], actor, fixture_root) if pre and pre.get('native_record') else None
    # v9 D4：导出前输出须与固定只读文件查询的原生调用配对，文字自报不能证明查询。
    probe_call = None
    try:
        if pre and pre.get('native_call'):
            probe_call = native(pre['native_call'], actor, fixture_root)
        probe_ok, probe_why = verify_pre_probe_pair(probe_call, pr, pre or {}, p['path'])
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as e:
        probe_ok, probe_why = False, 'pre-export paired filesystem-query evidence unknown: %s' % type(e).__name__
    INFO['pdf_preflight'] = 'PAIRED_IMMUTABLE_FILESYSTEM_PROBE' if probe_ok else 'UNKNOWN'
    if not pr: pend.append('PDF pre-export identity is not bound to a native original-actor output record; write causality not proven')
    elif not probe_ok: pend.append(probe_why)
    elif pr['type'] not in OUTS or pr['src'] != ec['src'] or pr['off'] >= ec['off'] or pr['t'] > ec['t'] or not names(pr['out'], p['path']): pend.append('pre-export native record is not an earlier output of the export rollout naming the PDF path')
    elif pre.get('existed'):
        if not re.fullmatch(r'[0-9a-f]{64}', str(pre.get('sha256'))) or pre['sha256'] not in pr['out']: pend.append('pre-export native output does not show the declared prior PDF SHA')
        elif pre['sha256'] == p['sha256']: pend.append('PDF has the same SHA as before export; overwrite causality not proven')
        else: INFO['pdf_identity'] = 'OVERWRITTEN_CHANGED'
    elif not re.search(r'No such file|不存在|does not exist|FileNotFoundError', pr['out']): pend.append('pre-export native output does not show the PDF absent')
    elif not (pr['t'] <= born and ec['t'] - 2 <= born <= eo['t'] + 120): pend.append('PDF birthtime does not match declared pre_export existed=false; write causality not proven')
    else: INFO['pdf_identity'] = 'NEW_IN_EXPORT_WINDOW'
    # F4：导出链首尾之间出现链外工具调用即 PENDING
    ids = {c['call_id'] for c, o in got['export']}; lo, hi = got['export'][0][0]['off'], got['export'][-1][1]['off'] + got['export'][-1][1]['nb']
    with open(got['export'][0][0]['src'], 'rb') as f: f.seek(lo); span = f.read(hi - lo)
    for line in span.splitlines():
        # v8 F4：未知行保留 PENDING，不能跳过或让 AttributeError 代替判定。
        q = logpayload(line)
        if q is None:
            pend.append('unparseable record within the export chain; integrity unknown'); break
        if q.get('type') in CALLS and q.get('call_id') not in ids: pend.append('tool call %s outside the export chain sits between its steps' % q.get('call_id')); break
    if n is not None and pages(p['path']) != n: fails.append('PDF pages != native reopen page count')
    if fails: return 'FAIL', reads, fails
    if pend: return 'ROUNDTRIP_PENDING_ACTION_EVIDENCE', reads, pend
    return ('FIXTURE_ONLY' if fixture_root else 'ROUNDTRIP_VERIFIED'), reads, []
if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('evidence'); ap.add_argument('--actor-session'); ap.add_argument('--fixture-root')
    a = ap.parse_args()
    if not a.actor_session and not a.fixture_root: ap.error('--actor-session is required (original actor Codex session id)')
    ev = json.load(open(a.evidence))
    try: st, reads, fails = verify(ev, a.actor_session, a.fixture_root)
    except (ValueError, OSError, KeyError, TypeError) as e: st, reads, fails = 'FAIL', [], ['%s: %s' % (type(e).__name__, e)]
    print(json.dumps(dict(status=st, reads=reads, failures=fails, actor_session=a.actor_session, fixture_mode=bool(a.fixture_root),
                          ignored_non_evidence=sorted(k for k in ev if k in IGNORED), **INFO), ensure_ascii=False))
    sys.exit({'ROUNDTRIP_VERIFIED': 0, 'NATIVE_READ_OK_ROUNDTRIP_PENDING': 3, 'ROUNDTRIP_PENDING_ACTION_EVIDENCE': 3, 'FIXTURE_ONLY': 4}.get(st, 1))
