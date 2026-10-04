"""Task-prompt journal: persist input intent; never repaste an uncertain dispatch."""
import contextlib
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from cmux_callback_journal import write_json


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def deliver(bridge, surface, text, task_pack_path, marker=None, confirm_lines=200,
            force_compose=False, *, reconcile_only=False):
    from availability_contract import require_action
    pack = bridge.validate_task_pack_contract(task_pack_path, prompt_text=text)
    require_action(pack['task_id'], 'dispatch', pack)
    marker = marker or pack['task_id']
    if not isinstance(marker, str) or not marker or marker not in text:
        raise bridge.TaskPackContractError('DISPATCH_MARKER_REQUIRED: bind a visible payload marker')
    if force_compose:
        raise bridge.TaskPackContractError('DISPATCH_PRESERVE_COMPOSE: resolve the original draft first')
    proof = bridge.pin_workspace(surface)
    identity = {k: proof[k] for k in ('workspace_uuid', 'caller_surface_uuid',
                                    'target_surface_uuid', 'target_pane_uuid')}
    if pack.get('executor_uuid') and pack['executor_uuid'].upper() != identity['target_surface_uuid'].upper():
        raise bridge.TaskPackContractError('DISPATCH_WRONG_EXECUTOR')
    binding = dict(task_id=pack['task_id'], marker=marker, payload_sha256=digest(text),
                   task_pack_sha256=bridge._sha256_file(task_pack_path), identity=identity)
    root = Path.home() / '.local/state/multi-agent-collaboration/task-dispatch-v1'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Identity and task, not content: editing a prompt must not create a new slot.
    key = digest(json.dumps([identity['caller_surface_uuid'], pack['task_id']]))
    journal = root / key
    journal.mkdir(exist_ok=True, mode=0o700)

    def recheck():
        current = bridge.pin_workspace(surface)
        if any(current.get(k) != v for k, v in identity.items()):
            raise bridge.TaskPackContractError('DISPATCH_IDENTITY_CHANGED')
        if bridge._sha256_file(task_pack_path) != binding['task_pack_sha256']:
            raise bridge.TaskPackContractError('DISPATCH_PACK_CHANGED')
        require_action(pack['task_id'], 'dispatch', pack)

    with contextlib.ExitStack() as stack:
        for path in (root / ('target-' + digest(identity['target_surface_uuid']) + '.lock'),
                     journal / 'delivery.lock'):
            lock = stack.enter_context(path.open('a+b'))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise bridge.TaskPackContractError('DISPATCH_IN_PROGRESS') from exc
        # Never steal a delivery10 attempt or reinterpret its historical state.
        old_key = digest(json.dumps([identity['caller_surface_uuid'], marker], sort_keys=True))
        legacy = root.parent / 'deliveries-v1' / (old_key + '.json')
        if legacy.exists():
            raise bridge.TaskPackContractError('ORIGINAL_DELIVERY_CONTROLLER_REQUIRED: ' + str(legacy))
        attempts = sorted(journal.glob('attempt-*.json'))
        old = json.loads(attempts[-1].read_text()) if attempts else None
        if old and old.get('binding') != binding:
            raise bridge.TaskPackContractError('DISPATCH_BINDING_CHANGED: preserve original attempt')
        receipt = journal / 'receipt.json'
        if receipt.exists():
            raise bridge.TaskPackContractError('DISPATCH_RECEIPT_EXISTS: no duplicate task')
        recheck()
        if reconcile_only:
            if not old or old.get('phase') in ('PREPARED', 'NO_INPUT'):
                raise bridge.TaskPackContractError('NO_SUBMITTED_DISPATCH')
            screen = bridge.read_screen(surface, lines=confirm_lines)
            observation = journal / ('observation-' + str(time.time_ns()) + '.json')
            write_json(observation, dict(screen=screen, screen_sha256=bridge.screen_hash(screen),
                                         input_operations=0, at_epoch=time.time()))
            before = next(e['screen'] for e in old['events'] if e['phase'] == 'PASTE_INTENT')
            if not bridge._delivery_confirmed(before, screen, marker, text):
                raise bridge.DispatchUnconfirmed('DISPATCH_NOT_YET_CONFIRMED: observe original; no resend')
            result = dict(confirmed=True, reconciled_read_only=True, observation=str(observation))
            attempt_path = attempts[-1]
        else:
            if old and old.get('phase') != 'NO_INPUT':
                raise bridge.TaskPackContractError('DISPATCH_ATTEMPT_EXISTS: use --reconcile-only')
            if len(attempts) >= 2:
                raise bridge.TaskPackContractError('DISPATCH_RETRY_BUDGET_EXHAUSTED')
            attempt_path = journal / ('attempt-%04d.json' % (len(attempts) + 1))
            attempt = dict(binding=binding, phase='PREPARED', events=[], started_at_epoch=time.time())
            write_json(attempt_path, attempt)

            def observe(phase, screen=None):
                recheck()
                attempt['phase'] = phase
                event = dict(phase=phase, at_epoch=time.time())
                if screen is not None:
                    event.update(screen=screen, screen_sha256=bridge.screen_hash(screen))
                attempt['events'].append(event)
                write_json(attempt_path, attempt)

            try:
                result = bridge.submit_text(surface, text, marker=marker, confirm_lines=confirm_lines,
                                            task_pack_path=task_pack_path, delivery_observer=observe)
                if result.get('confirmed') is not True:
                    raise bridge.DispatchUnconfirmed('DISPATCH_NOT_CONFIRMED')
            except BaseException as exc:
                if attempt['phase'] == 'PREPARED':
                    attempt['phase'] = 'NO_INPUT'
                attempt.update(error=str(exc), ended_at_epoch=time.time())
                write_json(attempt_path, attempt)
                raise
            attempt.update(phase='CONFIRMED', ended_at_epoch=time.time())
            write_json(attempt_path, attempt)
        recheck()
        result = dict(result, binding=binding, attempt=str(attempt_path), at_epoch=time.time())
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        return dict(result, receipt=str(receipt))
