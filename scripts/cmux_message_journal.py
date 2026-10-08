"""Durable ordinary messages: ambiguous sends may only be reconciled read-only."""
import contextlib
import fcntl
import hashlib
import json
import time
from pathlib import Path

from cmux_callback_journal import write_json
import cmux_native_delivery as native
from cmux_evidence_io import read_bytes, attempt_paths, open_regular_lock


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verified_receipt(bridge, surface, text, marker):
    """Read only: revalidate original dispatch evidence, never synthesize a receipt."""
    from delivery_receipts import snapshot
    try:
        if not isinstance(marker, str) or not marker or marker not in text:
            return None
        live = bridge.pin_workspace(surface)
        identity = {k: live[k] for k in ('workspace_uuid', 'caller_surface_uuid',
                                        'target_surface_uuid', 'target_pane_uuid')}
        if not all(isinstance(v, str) and v for v in identity.values()):
            return None
        binding = dict(identity=identity, marker=marker, payload_sha256=digest(text))
        root = Path.home() / ".local/state/multi-agent-collaboration/message-dispatch-v1"
        journal = root / digest(json.dumps([identity["caller_surface_uuid"], marker], sort_keys=True))
        receipt_path = journal / 'receipt.json'
        receipt_pin = snapshot(receipt_path)
        receipt = json.loads(read_bytes(receipt_path))
        attempts = attempt_paths(journal)
        if not attempts or receipt.get('attempt') != str(attempts[-1]):
            return None
        attempt_path = attempts[-1]
        attempt_pin = snapshot(attempt_path)
        attempt = json.loads(read_bytes(attempt_path))
        if (receipt.get('confirmed') is not True or receipt.get('binding') != binding
                or attempt.get('binding') != binding):
            return None
        events = attempt['events']
        pastes = [e for e in events if e['phase'] == 'PASTE_INTENT']
        if len(pastes) != 1:
            return None
        before = pastes[0]['screen']
        if pastes[0].get('screen_sha256') != bridge.screen_hash(before):
            return None
        pins = [(receipt_path, receipt_pin), (attempt_path, attempt_pin)]
        if receipt.get('reconciled_read_only') is True:
            observation_path = Path(receipt['observation'])
            if observation_path.parent != journal or not observation_path.name.startswith('observation-'):
                return None
            observation_pin = snapshot(observation_path)
            observation = json.loads(read_bytes(observation_path))
            if type(observation.get('input_operations')) is not int or observation['input_operations'] != 0:
                return None
            pins.append((observation_path, observation_pin))
        if not native.receipt_evidence(bridge, surface, text, attempt, receipt):
            return None
        if (attempt_paths(journal) != attempts or any(snapshot(p) != pin for p, pin in pins)
                or bridge.pin_workspace(surface) != live):
            return None
        return dict(source='revalidated_message_dispatch_v1', identity=identity,
                    attempt=attempt_pin, receipt=receipt_pin)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None


def deliver(bridge, surface, text, marker, confirm_lines=200, *, reconcile_only=False,
            recover_stranded=False):
    if not isinstance(marker, str) or not marker or marker not in text:
        raise bridge.TaskPackContractError('MESSAGE_MARKER_REQUIRED')
    if bridge._looks_like_task_dispatch(text):
        raise bridge.TaskPackContractError('TASK_PACK_REQUIRED')
    proof = bridge.pin_workspace(surface)
    keys = ('workspace_uuid', 'caller_surface_uuid', 'target_surface_uuid', 'target_pane_uuid')
    identity = {k: proof[k] for k in keys}
    if not all(isinstance(v, str) and v for v in identity.values()):
        raise bridge.TaskPackContractError('MESSAGE_IDENTITY_REQUIRED')
    binding = dict(identity=identity, marker=marker, payload_sha256=digest(text))
    root = Path.home() / '.local/state/multi-agent-collaboration'
    key = digest(json.dumps([identity['caller_surface_uuid'], marker], sort_keys=True))
    journal = root / 'message-dispatch-v1' / key
    journal.mkdir(parents=True, exist_ok=True, mode=0o700)

    def recheck():
        live = bridge.pin_workspace(surface)
        if any(live.get(k) != v for k, v in identity.items()):
            raise bridge.TaskPackContractError('MESSAGE_IDENTITY_CHANGED')

    with contextlib.ExitStack() as stack:
        # Share the existing generic sender lock, so old and new controllers
        # cannot race on the same receiver. Old attempts retain their controller.
        legacy_root = root / 'deliveries-v1'
        legacy_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        task_root = root / 'task-dispatch-v1'
        task_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in (legacy_root / ('pane-' + digest(identity['workspace_uuid'] + ':' + identity['target_pane_uuid']) + '.lock'),
                     legacy_root / ('target-' + digest(identity['target_surface_uuid']) + '.lock'),
                     task_root / ('target-' + digest(identity['target_surface_uuid']) + '.lock'),
                     journal / 'delivery.lock'):
            lock = stack.enter_context(open_regular_lock(path))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise bridge.TaskPackContractError('MESSAGE_IN_PROGRESS') from exc
        legacy = legacy_root / (key + '.json')
        if legacy.exists():
            raise bridge.TaskPackContractError('ORIGINAL_DELIVERY_CONTROLLER_REQUIRED: ' + str(legacy))
        attempts = attempt_paths(journal)
        old = json.loads(read_bytes(attempts[-1])) if attempts else None
        if old and old.get('binding') != binding:
            raise bridge.TaskPackContractError('MESSAGE_BINDING_CHANGED')
        if (journal / 'receipt.json').exists():
            raise bridge.TaskPackContractError('MESSAGE_RECEIPT_EXISTS: no duplicate send')
        recheck()
        if recover_stranded:
            # Enter is not delivery: an earlier attempt pasted once and pressed
            # Enter, but the payload stayed in the receiver composer. Allow one
            # extra key on that same attempt, never a second paste.
            if reconcile_only or not old:
                raise bridge.TaskPackContractError('NO_STRANDED_MESSAGE')
            phases = [e['phase'] for e in old.get('events', [])]
            if (phases.count('PASTE_INTENT') != 1 or 'ENTER_SENT' not in phases
                    or old.get('phase') in ('PREPARED', 'NO_INPUT', 'CONFIRMED')):
                raise bridge.TaskPackContractError('NO_STRANDED_MESSAGE')
            if 'QUEUE_TAB_INTENT' in phases or 'EXTRA_ENTER_INTENT' in phases:
                raise bridge.TaskPackContractError('STRANDED_RECOVERY_ALREADY_USED: reconcile_only')
            paste = next(e for e in old['events'] if e['phase'] == 'PASTE_INTENT')
            if paste.get('screen_sha256') != bridge.screen_hash(paste['screen']):
                raise bridge.TaskPackContractError('MESSAGE_EVIDENCE_CHANGED')
            attempt_path, attempt = attempts[-1], old

            def observe_recovery(phase, screen=None):
                recheck()
                event = dict(phase=phase, at_epoch=time.time(), recovery='stranded_compose')
                if screen is not None:
                    event.update(screen=screen, screen_sha256=bridge.screen_hash(screen))
                attempt['events'].append(event)
                attempt['phase'] = phase
                write_json(attempt_path, attempt)

            try:
                result = bridge.recover_stranded_once(surface, text, marker, paste['screen'],
                    confirm_lines=confirm_lines, delivery_observer=observe_recovery,
                    native_binding=old.get('native_binding'), native_attempt=attempt_path)
            except BaseException as exc:
                attempt['recovery_error'] = str(exc)
                write_json(attempt_path, attempt)
                raise
            attempt['phase'] = 'CONFIRMED'
            write_json(attempt_path, attempt)
        elif reconcile_only:
            pastes = [e for e in (old or {}).get('events', []) if e['phase'] == 'PASTE_INTENT']
            if len(pastes) != 1:
                raise bridge.TaskPackContractError('NO_SUBMITTED_MESSAGE')
            before = pastes[0]['screen']
            if pastes[0].get('screen_sha256') != bridge.screen_hash(before):
                raise bridge.TaskPackContractError('MESSAGE_EVIDENCE_CHANGED')
            result = native.scan_original(bridge, surface, text, attempts[-1], old.get('native_binding'))
            observation = journal / ('observation-' + str(time.time_ns()) + '.json')
            write_json(observation, dict(result, input_operations=0, at_epoch=time.time()))
            if result.get('confirmed') is not True:
                raise bridge.DispatchUnconfirmed('MESSAGE_NOT_YET_CONFIRMED: no resend')
            attempt_path = attempts[-1]
            result = dict(result, reconciled_read_only=True, observation=str(observation))
        else:
            if old and old.get('phase') != 'NO_INPUT':
                raise bridge.TaskPackContractError('MESSAGE_ATTEMPT_EXISTS: use reconcile_only')
            if len(attempts) >= 2:
                raise bridge.TaskPackContractError('MESSAGE_RETRY_BUDGET_EXHAUSTED')
            attempt_path = journal / ('attempt-%04d.json' % (len(attempts) + 1))
            attempt = dict(binding=binding, phase='PREPARED', events=[])
            write_json(attempt_path, attempt)

            def observe(phase, screen=None):
                recheck()
                # Refuse stale nonce before the very first mutation.
                if phase == 'PASTE_INTENT' and ''.join(marker.split()) in ''.join(screen.split()):
                    raise bridge.TaskPackContractError('MESSAGE_MARKER_ALREADY_VISIBLE')
                fence = native.capture_paste_fence(attempt['native_binding']) if phase == 'PASTE_INTENT' else None
                event = dict(phase=phase, at_epoch=time.time())
                if fence is not None:
                    event['native_paste_fence'] = fence
                if screen is not None:
                    event.update(screen=screen, screen_sha256=bridge.screen_hash(screen))
                attempt['events'].append(event)
                attempt['phase'] = phase
                write_json(attempt_path, attempt)

            try:
                attempt['native_binding'] = native.bind_target(bridge, surface, text)
                write_json(attempt_path, attempt)
                result = bridge._submit_text_once(surface, text, marker=marker,
                    confirm_lines=confirm_lines, force_compose=False, delivery_observer=observe,
                    native_binding=attempt['native_binding'], native_attempt=attempt_path)
                if result.get('confirmed') is not True:
                    raise bridge.DispatchUnconfirmed('MESSAGE_NOT_CONFIRMED')
            except BaseException as exc:
                if attempt['phase'] == 'PREPARED':
                    attempt['phase'] = 'NO_INPUT'
                attempt['error'] = str(exc)
                write_json(attempt_path, attempt)
                raise
            attempt['phase'] = 'CONFIRMED'
            write_json(attempt_path, attempt)
        recheck()
        if not native.receipt_evidence(bridge, surface, text, json.loads(read_bytes(attempt_path)), result):
            raise bridge.DispatchUnconfirmed('MESSAGE_NATIVE_PROOF_REQUIRED')
        result = dict(result, binding=binding, attempt=str(attempt_path), at_epoch=time.time())
        receipt = journal / 'receipt.json'
        write_json(receipt, result)
        return dict(result, receipt=str(receipt))
