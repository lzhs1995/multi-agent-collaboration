#!/usr/bin/env python3
"""One explicit ordinary-message recovery after two proven pre-input refusals.

Default: inspect the exact history without creating a journal or sending input.
Apply: require the reviewed history SHA and reserve one irreversible recovery
slot before using the complete original controller's protected send and reader.
Original attempts, payload, endpoints and controller never change. A crash or
another NO_INPUT on this third attempt never supplies another recovery budget.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import stat
import time

from repair_task_notice import controller_modules

SCHEMA = 'ordinary-message-zero-input-resume-v1'
KEYS = ('workspace_uuid', 'caller_surface_uuid', 'target_surface_uuid', 'target_pane_uuid')
# These complete releases share the reviewed protected ordinary-message driver.
# Older/future driver formats require a separate compatibility review.
DRIVER_SHA256 = '1157b5797077be2acd70bfa09f8fe95f8390254199127e5ce580c492462514db'
SUPPORTED_DRIVERS = {'0.4.%d' % version: DRIVER_SHA256 for version in range(58, 83)}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def history_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('MESSAGE_RESUME_DUPLICATE_HISTORY_KEY')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('MESSAGE_RESUME_INVALID_HISTORY_NUMBER')

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('MESSAGE_RESUME_INVALID_HISTORY_NUMBER')
        return parsed

    try:
        row = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                         parse_constant=constant, parse_float=number)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('MESSAGE_RESUME_INVALID_HISTORY_JSON') from exc
    if not isinstance(row, dict):
        raise ValueError('MESSAGE_RESUME_HISTORY_OBJECT_REQUIRED')
    return row


def original_driver(source, evidence):
    entry = source / 'scripts/cmux_message_journal.py'
    version = evidence.read_bytes(source / 'VERSION').decode('utf-8').strip()
    digest = sha(evidence.read_bytes(entry))
    if SUPPORTED_DRIVERS.get(version) != digest:
        raise ValueError('MESSAGE_RESUME_UNSUPPORTED_ORIGINAL_DRIVER')
    return dict(source=str(source), entrypoint=str(entry),
                entrypoint_sha256=digest, version=version)


def history_pin(records, binding, locks, controller):
    members = [dict(name=p.name, bytes=len(raw), sha256=sha(raw)) for p, raw in records]
    value = dict(attempts=members, binding=binding, locks=locks, controller=controller)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return dict(value, history_sha256=sha(raw))


def validate_history(records, binding, controller):
    if len(records) != 2:
        raise ValueError('MESSAGE_RESUME_REQUIRES_EXACTLY_TWO_ORIGINAL_ATTEMPTS')
    rows = []
    for number, (path, raw) in enumerate(records, 1):
        if path.name != 'attempt-%04d.json' % number:
            raise ValueError('MESSAGE_RESUME_HISTORY_NOT_CONTIGUOUS')
        row = history_json(raw)
        if (row.get('phase') != 'NO_INPUT' or row.get('events') != []
                or 'result' in row or not isinstance(row.get('error'), str)
                or not row['error'] or 'zero_input_resume' in row):
            raise ValueError('MESSAGE_RESUME_HISTORY_HAS_INPUT_OR_UNKNOWN')
        details = row.get('error_details', {})
        if not isinstance(details, dict) or any(
                type(value) is not int or value != 0 for value in
                [row.get('input_operations', 0), details.get('input_operations', 0)]):
            raise ValueError('MESSAGE_RESUME_HISTORY_HAS_INPUT_OR_UNKNOWN')
        if row.get('binding') != binding:
            raise ValueError('MESSAGE_RESUME_ORIGINAL_BINDING_CHANGED')
        provenance = row.get('controller_provenance')
        if (not isinstance(provenance, dict)
                or provenance.get('source') != controller['source']
                or provenance.get('entrypoint') != controller['entrypoint']
                or provenance.get('entrypoint_sha256') != controller['entrypoint_sha256']):
            raise ValueError('MESSAGE_RESUME_ORIGINAL_CONTROLLER_REQUIRED')
        rows.append(row)
    # A native observation failure may precede binding on one refusal. Keep
    # that absence honest; at least one original authenticated runtime binding
    # is required. Never manufacture a missing session/process/EOF binding.
    known = [r['native_binding'] for r in rows if isinstance(r.get('native_binding'), dict)]
    if not known:
        raise ValueError('MESSAGE_RESUME_ORIGINAL_NATIVE_BINDING_REQUIRED')
    for original in known:
        if (original.get('identity') != binding['identity']
                or original.get('payload_sha256') != binding['payload_sha256']):
            raise ValueError('MESSAGE_RESUME_ORIGINAL_NATIVE_BINDING_CHANGED')
        if any(original.get(k) != known[0].get(k) for k in ('provider','session_id','process','tty','identity')):
            raise ValueError('MESSAGE_RESUME_ORIGINAL_RUNTIME_CHANGED')
        if any(original.get('transcript',{}).get(k) != known[0].get('transcript',{}).get(k)
               for k in ('path','device','inode')):
            raise ValueError('MESSAGE_RESUME_ORIGINAL_TRANSCRIPT_CHANGED')
    return rows, known


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exclusive(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode() + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    _sync(path.parent)


def _lock(path, stack):
    # Missing locks cannot be replaced to make a read-only preview pass.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    handle = stack.enter_context(os.fdopen(fd, 'rb'))
    held, current = os.fstat(handle.fileno()), path.lstat()
    if (not stat.S_ISREG(held.st_mode) or held.st_nlink != 1
            or held.st_uid != os.getuid()
            or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
        raise ValueError('MESSAGE_RESUME_ORIGINAL_LOCK_CHANGED')
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return dict(path=str(path), device=held.st_dev, inode=held.st_ino)


def resume(args, modules=None):
    bridge, native, evidence, journal_io, _availability = modules or controller_modules(args.controller)
    # Import operational helpers only after the complete original controller
    # loader has selected its source. The recovery coordinator substitutes none.
    scripts = Path(bridge.__file__).resolve().parent
    message = importlib.import_module('cmux_message_journal')
    prompt = importlib.import_module('cmux_prompt_reference')
    confirmation = importlib.import_module('cmux_confirmation')
    if any(Path(m.__file__).resolve().parent != scripts for m in (native, message, prompt, confirmation)):
        raise ValueError('MESSAGE_RESUME_MIXED_CONTROLLER')
    entry = scripts / 'cmux_message_journal.py'
    controller = original_driver(Path(args.controller), evidence)
    if str(scripts.parent) != controller['source']:
        raise ValueError('MESSAGE_RESUME_ORIGINAL_CONTROLLER_REQUIRED')
    text_path = Path(args.text_file)
    if not text_path.is_absolute():
        raise ValueError('MESSAGE_RESUME_ABSOLUTE_TEXT_FILE_REQUIRED')
    text_raw = evidence.read_bytes(text_path)
    text = text_raw.decode('utf-8')
    marker = args.marker
    if not isinstance(marker, str) or not marker or marker not in text:
        raise ValueError('MESSAGE_RESUME_ORIGINAL_MARKER_REQUIRED')
    if bridge._looks_like_task_dispatch(text):
        raise ValueError('MESSAGE_RESUME_ORDINARY_MESSAGES_ONLY')
    prompt.require_inline(text)
    if args.apply and not args.history_sha256:
        raise ValueError('MESSAGE_RESUME_REVIEWED_HISTORY_SHA256_REQUIRED')
    seconds = confirmation.wait_budget(args.wait_seconds, reconcile_only=False)
    live = bridge.pin_workspace(args.surface)
    identity = {k: live[k] for k in KEYS}
    if not all(isinstance(v,str) and v for v in identity.values()):
        raise ValueError('MESSAGE_RESUME_ORIGINAL_IDENTITY_REQUIRED')
    binding = dict(identity=identity, marker=marker, payload_sha256=sha(text_raw))
    root = Path.home() / '.local/state/multi-agent-collaboration'
    legacy = root / 'deliveries-v1'
    task = root / 'task-dispatch-v1'
    digest = lambda text: sha(text.encode())
    key = digest(json.dumps([identity['caller_surface_uuid'],marker],sort_keys=True))
    directory = root / 'message-dispatch-v1' / key
    receipt = directory / 'receipt.json'
    slot = directory / 'zero-input-recovery.json'
    legacy_attempt = legacy / (key+'.json')
    lock_paths = (
        legacy / ('pane-'+digest(identity['workspace_uuid']+':'+identity['target_pane_uuid'])+'.lock'),
        legacy / ('target-'+digest(identity['target_surface_uuid'])+'.lock'),
        task / ('target-'+digest(identity['target_surface_uuid'])+'.lock'),
        directory / 'delivery.lock')
    with contextlib.ExitStack() as stack:
        locks = [_lock(p,stack) for p in lock_paths]
        for p in (directory, directory.parent):
            s = p.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid():
                raise ValueError('MESSAGE_RESUME_ORIGINAL_DIRECTORY_REQUIRED')
            locks.append(dict(path=str(p),device=s.st_dev,inode=s.st_ino,directory=True))
        if os.path.lexists(slot):
            raise ValueError('MESSAGE_RESUME_RECOVERY_ALREADY_USED: reconcile original attempt')
        paths = evidence.attempt_paths(directory)
        records = [(p,evidence.read_bytes(p)) for p in paths]
        rows,known = validate_history(records,binding,controller)
        plan = history_pin(records,binding,locks,controller)
        if args.history_sha256 and args.history_sha256 != plan['history_sha256']:
            raise ValueError('MESSAGE_RESUME_REVIEWED_HISTORY_CHANGED')
        body_pins = [message.original_body_pin(text,row) for row in rows]
        expected_paths = list(paths)

        def recheck():
            if os.path.lexists(receipt) or os.path.lexists(legacy_attempt):
                raise ValueError('MESSAGE_RESUME_RECEIPT_OR_LEGACY_DELIVERY_EXISTS')
            if evidence.attempt_paths(directory) != expected_paths:
                raise ValueError('MESSAGE_RESUME_HISTORY_CHANGED')
            if (evidence.read_bytes(text_path) != text_raw
                    or any(evidence.read_bytes(p) != raw for p,raw in records)
                    or original_driver(Path(args.controller), evidence) != controller):
                raise ValueError('MESSAGE_RESUME_ORIGINAL_BYTES_CHANGED')
            current_live = bridge.pin_workspace(args.surface)
            if any(current_live.get(k)!=v for k,v in identity.items()):
                raise ValueError('MESSAGE_RESUME_LIVE_CALLER_CHANGED')
            for item in locks:
                s=Path(item['path']).lstat()
                kind=stat.S_ISDIR if item.get('directory') else stat.S_ISREG
                if (not kind(s.st_mode) or s.st_uid!=os.getuid()
                        or (s.st_dev,s.st_ino)!=(item['device'],item['inode'])
                        or (not item.get('directory') and s.st_nlink!=1)):
                    raise ValueError('MESSAGE_RESUME_LOCK_CHANGED')
            for row,pin in zip(rows,body_pins):
                if message.original_body_pin(text,row)!=pin:
                    raise ValueError('MESSAGE_RESUME_ORIGINAL_BODY_CHANGED')
            native.require_bound(bridge,args.surface,known[-1],text)

        recheck()
        plan.update(schema=SCHEMA,journal=str(directory),
                    original_missing_native_bindings=len(rows)-len(known),
                    recovery_budget=1,original_attempts_preserved=True)
        if not args.apply:
            return dict(status='READY_ZERO_INPUT',plan=plan,input_operations=0)
        # Exclusive slot + directory sync consume the single budget even if
        # the process dies before creating the third attempt or fresh binding.
        _exclusive(slot,dict(plan,state='BUDGET_CONSUMED',reserved_at_epoch=time.time()))
        attempt_path=directory/'attempt-0003.json'
        attempt=dict(binding=binding,phase='PREPARED',events=[],
                     controller_provenance=rows[0]['controller_provenance'],
                     zero_input_resume=plan,started_at_epoch=time.time())
        if 'body_reference' in rows[0]:
            attempt.update(body_reference=rows[0]['body_reference'],body_pin=body_pins[0])
        _exclusive(attempt_path,attempt)
        expected_paths.append(attempt_path)

        def observe(phase,screen=None,*,evidence=None):
            recheck()
            if phase=='PASTE_INTENT' and ''.join(marker.split()) in ''.join((screen or '').split()):
                raise ValueError('MESSAGE_RESUME_MARKER_ALREADY_VISIBLE')
            fields=native.input_event_fields(attempt['native_binding'],text,phase,evidence)
            event=dict(fields,phase=phase,at_epoch=time.time())
            if phase=='PASTE_INTENT':
                event['native_paste_fence']=native.capture_paste_fence(attempt['native_binding'])
                event['at_epoch']=time.time()
            if screen is not None:
                event.update(screen=screen,screen_sha256=bridge.screen_hash(screen))
            attempt['events'].append(event)
            attempt['phase']=phase
            journal_io.write_json(attempt_path,attempt)

        try:
            fresh=native.bind_target(bridge,args.surface,text)
            for original in known:
                if any(fresh.get(k)!=original.get(k) for k in ('provider','session_id','process','tty','identity')):
                    raise ValueError('MESSAGE_RESUME_ORIGINAL_RUNTIME_CHANGED')
                if any(fresh['transcript'].get(k)!=original['transcript'].get(k) for k in ('path','device','inode')):
                    raise ValueError('MESSAGE_RESUME_ORIGINAL_TRANSCRIPT_CHANGED')
            attempt['native_binding']=native.marker_binding(fresh,text,marker)
            journal_io.write_json(attempt_path,attempt)
            recheck()
            result=bridge._submit_text_once(args.surface,text,marker=marker,
                force_compose=False,delivery_observer=observe,
                native_binding=attempt['native_binding'],native_attempt=attempt_path,
                wait_seconds=seconds)
            if result.get('confirmed') is not True:
                raise bridge.DispatchUnconfirmed('MESSAGE_RESUME_NATIVE_RECEIPT_PENDING',
                    state=bridge.native_submission_state(result),details=result)
            recheck()
            if not native.receipt_evidence(bridge,args.surface,text,attempt,result):
                raise bridge.DispatchUnconfirmed('MESSAGE_RESUME_NATIVE_PROOF_REQUIRED')
            attempt.update(phase='CONFIRMED',ended_at_epoch=time.time())
            journal_io.write_json(attempt_path,attempt)
            result=confirmation.attempt_details(result,attempt_path,attempt)
            result.update(binding=binding,attempt=str(attempt_path),at_epoch=time.time(),zero_input_resume=plan)
            if 'body_reference' in attempt:
                result.update(confirmation_scope='reference_notice',body_read_confirmed=False,
                              body_reference=attempt['body_reference'])
            _exclusive(receipt,result)
            return dict(result,receipt=str(receipt),original_attempts_preserved=True)
        except BaseException as exc:
            if attempt['phase']=='PREPARED':
                attempt['phase']='NO_INPUT'
            confirmation.annotate_attempt_error(exc,attempt_path,attempt)
            attempt.update(error=str(exc),error_details=exc.details,ended_at_epoch=time.time())
            journal_io.write_json(attempt_path,attempt)
            raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('controller','surface','marker','text-file'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--history-sha256')
    p.add_argument('--apply',action='store_true')
    p.add_argument('--wait-seconds',type=float,default=0)
    args=p.parse_args()
    try:
        print(json.dumps(resume(args),ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
        details=getattr(exc,'details',{})
        print(json.dumps(dict(status='NOT_CONFIRMED',error=str(exc),details=details,
                              attempt=getattr(exc,'attempt',None)),ensure_ascii=False))
        return 75 if getattr(exc,'details',{}).get('input_operations',0) else 2


if __name__=='__main__':
    raise SystemExit(main())
