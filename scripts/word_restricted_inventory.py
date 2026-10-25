"""Read-only metadata. Missing foreign references remain explicit UNKNOWN."""
SCRIPT = '''on boundProcess(thePID, expectedBundle)
tell application "System Events"
set candidates to every application process whose unix id is thePID
if (count candidates) is not 1 then error "BOUND_PROCESS_NOT_UNIQUE"
set procRef to item 1 of candidates
if bundle identifier of procRef is not expectedBundle then error "BOUND_BUNDLE_MISMATCH"
return procRef
end tell
end boundProcess
on clean(v)
set v to v as text
if v contains tab or v contains linefeed or v contains return then error "UNSUPPORTED_METADATA_TEXT"
return v
end clean
on run argv
if (count argv) is not 2 then error "EXPECTED_WORD_ZOTERO_PIDS"
set wp to (item 1 of argv) as integer
set zp to (item 2 of argv) as integer
if not (application "Microsoft Word" is running) then error "WORD_NOT_RUNNING"
with timeout of 12 seconds
tell application "System Events"
set wordProcess to my boundProcess(wp, "com.microsoft.Word")
set zoteroProcess to my boundProcess(zp, "org.zotero.zotero")
end tell
tell application "Microsoft Word"
set dc to count documents
set wc to count windows
set reportText to "WORD_RESTRICTED_INVENTORY_V1" & tab & dc & tab & wc & linefeed
repeat with di from 1 to dc
set n to "MISSING"
set p to "MISSING"
set s to "UNKNOWN"
try
set d to document di
try
set v to name of d
if (class of v) is text then set n to v
end try
try
set v to posix full name of d
if (class of v) is text and v is not "" then set p to v
end try
try
set v to saved of d
if (class of v) is boolean then set s to v as text
end try
end try
set reportText to reportText & "DOC" & tab & di & tab & my clean(n) & tab & my clean(p) & tab & s & linefeed
end repeat
repeat with wi from 1 to wc
set w to window wi
set p to "MISSING"
try
set v to posix full name of (document of w)
if (class of v) is text and v is not "" then set p to v
end try
set reportText to reportText & "WIN" & tab & wi & tab & my clean(p) & tab & my clean(name of w) & tab & ((visible of w) as text) & tab & my clean(window state of w) & linefeed
end repeat
set activePath to "MISSING"
try
if dc > 0 then
set v to posix full name of active document
if (class of v) is text and v is not "" then set activePath to v
end if
end try
set reportText to reportText & "ACTIVE" & tab & my clean(activePath) & linefeed
if (count documents) is not dc or (count windows) is not wc then error "WORD_SET_CHANGED"
end tell
tell application "System Events"
set appNames to {"Microsoft Word", "Zotero"}
set appProcesses to {wordProcess, zoteroProcess}
repeat with i from 1 to 2
set appName to item i of appNames
tell (item i of appProcesses)
set aws to windows
set reportText to reportText & "AXAPP" & tab & (contents of appName) & tab & (count aws) & linefeed
repeat with aw in aws
set reportText to reportText & "AXWIN" & tab & (contents of appName) & tab & my clean(name of aw) & tab & my clean(role of aw) & tab & my clean(subrole of aw) & tab & ((value of attribute "AXModal" of aw) as text) & tab & ((value of attribute "AXMinimized" of aw) as text) & tab & (count sheets of aw) & linefeed
end repeat
if (count windows) is not (count aws) then error "AX_SET_CHANGED"
end tell
end repeat
set wordProcess to my boundProcess(wp, "com.microsoft.Word")
set zoteroProcess to my boundProcess(zp, "org.zotero.zotero")
end tell
return reportText & "END"
end timeout
end run
'''


def _unsaved_bare_reference(reference, name, saved):
    # Word恢复稿可将显示名称当作posix full name返回；该值始终不当作文件路径。
    return (saved == 'false' and reference == name and reference not in ('.', '..')
            and 0 < len(reference) <= 4096
            and not any(c in reference for c in ('/', '\\', ':'))
            and all(ord(c) >= 32 and not 127 <= ord(c) <= 159 for c in reference))


def parse(text):
    from word_restricted_broker import need
    lines = text.strip().splitlines()
    need(len(lines) >= 2 and lines[-1] == 'END', 'COMPLETE_NATIVE_INVENTORY_REQUIRED')
    header = lines[0].split('\t')
    need(len(header) == 3 and header[0] == 'WORD_RESTRICTED_INVENTORY_V1', 'NATIVE_INVENTORY_HEADER_REQUIRED')
    counts = [int(v) for v in header[1:]]
    need(all(0 <= n <= 200 for n in counts), 'BOUNDED_NATIVE_INVENTORY_REQUIRED')
    result = dict(documents=[], windows=[], axapps=[], axwindows=[], active=[])
    unresolved = []
    for line in lines[1:-1]:
        f = line.split('\t')
        if f[0] == 'DOC':
            need(len(f) == 5 and f[1].isdigit() and f[2] and f[3] and f[4] in ('true','false','UNKNOWN'), 'NATIVE_DOCUMENT_METADATA_REQUIRED')
            if f[3] != 'MISSING' and not f[3].startswith('/'):
                need(_unsaved_bare_reference(f[3], f[2], f[4]),
                     'ABSOLUTE_OR_MISSING_DOCUMENT_REFERENCE_REQUIRED')
                # 原始DOC行保持不变；显式记录未解析、未知归属和不可写。
                unresolved.append(dict(document_index=f[1], raw_reference=f[3],
                    reference_status='UNRESOLVED_UNSAVED_BARE_NAME',
                    filesystem_path=None, ownership='UNKNOWN', writable=False))
            result['documents'].append(f[1:])
        elif f[0] == 'WIN':
            need(len(f) == 6 and f[1].isdigit() and f[4] in ('true','false'), 'NATIVE_WINDOW_METADATA_REQUIRED')
            result['windows'].append(f[1:])
        elif f[0] == 'ACTIVE':
            need(len(f) == 2, 'NATIVE_ACTIVE_METADATA_REQUIRED'); result['active'].append(f[1])
        elif f[0] == 'AXAPP':
            need(len(f) == 3 and f[1] in ('Microsoft Word','Zotero') and f[2].isdigit(), 'NATIVE_AX_APP_REQUIRED')
            result['axapps'].append(f[1:])
        elif f[0] == 'AXWIN':
            need(len(f) == 8 and f[1] in ('Microsoft Word','Zotero') and f[5] == 'false'
                 and f[6] in ('true','false') and f[7] == '0', 'NATIVE_MODAL_OR_SHEET_UNRESOLVED')
            result['axwindows'].append(f[1:])
        else:
            need(False, 'UNKNOWN_NATIVE_INVENTORY_RECORD')
    need(len(result['documents']) == counts[0] and len(result['windows']) == counts[1], 'NATIVE_INVENTORY_COUNT_MISMATCH')
    need([int(x[0]) for x in result['documents']] == list(range(1,counts[0]+1))
         and [int(x[0]) for x in result['windows']] == list(range(1,counts[1]+1)), 'NATIVE_INVENTORY_INDEX_MISMATCH')
    need(len(result['active']) == 1 and {x[0] for x in result['axapps']} == {'Microsoft Word','Zotero'}
         and len(result['axapps']) == 2, 'COMPLETE_NATIVE_WINDOW_SCOPE_REQUIRED')
    for app, count in result['axapps']:
        need(sum(x[0] == app for x in result['axwindows']) == int(count), 'NATIVE_AX_COUNT_MISMATCH')
    for entry in unresolved:
        reference = entry['raw_reference']
        # 只接纳能唯一对应同一原生文档和窗口的显示引用；歧义继续拒绝。
        need(sum(d[2] == reference for d in result['documents']) == 1
             and sum(w[1] == reference for w in result['windows']) == 1,
             'UNRESOLVED_NATIVE_REFERENCE_NOT_UNIQUE')
    if unresolved:
        # 生产消费者比较完整before/after；原始行和此分类都参加相等性检查。
        result['unresolved_document_references'] = unresolved
    return result
