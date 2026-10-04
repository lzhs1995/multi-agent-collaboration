"""Durable callback delivery; reconciliation never writes to a terminal."""
import fcntl
import json
import os
import time
from pathlib import Path


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def verified_receipt(bridge, surface, task_pack_path):
    """Validate the original journal read-only; never send or create a receipt."""
    from delivery_receipts import snapshot
    from cmux_submit_confirmation_guard import submission_target
    try:
        path = Path(task_pack_path)
        if not path.is_absolute():
            return None
        pins = {path: snapshot(path)}
        pack = bridge.validate_task_pack_contract(path)
        if submission_target(pack['callback_target']) != surface:
            return None
        live = bridge.pin_workspace(surface)
        identity = {k: live[k] for k in ('workspace_uuid', 'caller_surface_uuid',
                                       'target_surface_uuid', 'target_pane_uuid')}
        if not all(isinstance(v, str) and v for v in identity.values()):
            return None
        if identity['caller_surface_uuid'].upper() != pack['executor_uuid'].upper():
            return None
        report = Path(pack['report'])
        pins[report] = snapshot(report)
        binding = dict(task_id=pack['task_id'], completion_nonce=pack['completion_nonce'],
                       completion_callback=pack['completion_callback'],
                       callback_target=pack['callback_target'], task_pack_sha256=pins[path]['sha256'],
                       report=str(report), report_sha256=pins[report]['sha256'],
                       report_bytes=report.stat().st_size)
        receipt_path = Path(pack['completion_receipt'])
        pins[receipt_path] = snapshot(receipt_path)
        receipt = json.loads(receipt_path.read_text())
        journal = receipt_path.with_name(receipt_path.stem + '-attempts')
        attempts = sorted(journal.glob('attempt-*.json'))
        if not attempts or receipt.get('attempt') != str(attempts[-1]):
            return None
        pins[attempts[-1]] = snapshot(attempts[-1])
        attempt = json.loads(attempts[-1].read_text())
        if (receipt.get('confirmed') is not True
                or any(receipt.get(k) != v for k, v in binding.items())
                or attempt.get('binding') != dict(binding, identity=identity)):
            return None
        pastes = [e for e in attempt['events'] if e['phase'] == 'PASTE_INTENT']
        if len(pastes) != 1 or pastes[0].get('screen_sha256') != bridge.screen_hash(pastes[0]['screen']):
            return None
        if receipt.get('reconciled_read_only') is True:
            observed = Path(receipt['observation'])
            if observed.parent != journal or not observed.name.startswith('observation-'):
                return None
            pins[observed] = snapshot(observed)
            observation = json.loads(observed.read_text())
            if type(observation.get('input_operations')) is not int or observation['input_operations'] != 0:
                return None
        else:
            observations = [e for e in attempt['events'] if e['phase'] in
                            ('POST_ENTER_OBSERVATION', 'POST_QUEUE_TAB_OBSERVATION')]
            if attempt.get('phase') != 'CONFIRMED' or not observations:
                return None
            observation = observations[-1]
        after = observation['screen']
        if (observation.get('screen_sha256') != bridge.screen_hash(after)
                or not bridge._delivery_confirmed(pastes[0]['screen'], after,
                                                   pack['completion_nonce'], pack['completion_callback'])):
            return None
        if any(snapshot(p) != pin for p, pin in pins.items()) or bridge.pin_workspace(surface) != live:
            return None
        return dict(source='revalidated_callback_journal', identity=identity,
                    pack=pins[path], report=pins[report], receipt=pins[receipt_path])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None


def deliver(bridge, task_pack_path, confirm_lines=200, *, reconcile_only=False):
    from availability_contract import require_action
    pack = bridge.validate_task_pack_contract(task_pack_path)
    require_action(pack['task_id'], 'callback', pack)
    report = Path(pack['report'])
    if not report.is_file():
        raise bridge.TaskPackContractError('COMPLETION_CALLBACK_REFUSED: report must exist first')
    receipt_path = Path(pack['completion_receipt'])
    journal = receipt_path.with_name(receipt_path.stem + '-attempts')
    journal.mkdir(exist_ok=True)
    # Persistent inode; a second process must never race the same callback.
    with (journal / 'delivery.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise bridge.TaskPackContractError('CALLBACK_IN_PROGRESS: preserve original attempt') from exc
        if receipt_path.exists():
            raise bridge.TaskPackContractError('COMPLETION_RECEIPT_EXISTS: refusing duplicate callback')
        # Pre-journal releases persisted this file before any terminal input.
        # Its presence means delivery may already have happened, even if its
        # contents are truncated. Never silently migrate by sending again.
        legacy = Path(str(receipt_path) + '.pending.json')
        if legacy.exists():
            raise bridge.TaskPackContractError(
                'LEGACY_CALLBACK_PENDING: preserve original; supervisor must '
                'review actual receipt evidence without resending')
        proof = bridge.pin_workspace(pack['callback_target'])
        identity = {k: proof[k] for k in ('workspace_uuid', 'caller_surface_uuid',
                                        'target_surface_uuid', 'target_pane_uuid')}
        if (not pack.get('executor_uuid') or
                identity['caller_surface_uuid'].upper() != pack['executor_uuid'].upper()):
            raise bridge.TaskPackContractError('CALLBACK_WRONG_EXECUTOR: exact original executor required')
        binding = {
            'task_id': pack['task_id'], 'completion_nonce': pack['completion_nonce'],
            'completion_callback': pack['completion_callback'],
            'callback_target': pack['callback_target'],
            'task_pack_sha256': bridge._sha256_file(task_pack_path),
            'report': str(report), 'report_sha256': bridge._sha256_file(report),
            'report_bytes': report.stat().st_size,
            'identity': identity,
        }
        attempts = sorted(journal.glob('attempt-*.json'))
        old = json.loads(attempts[-1].read_text()) if attempts else None
        if old and old['binding'] != binding:
            raise bridge.TaskPackContractError('CALLBACK_BINDING_CHANGED: preserve previous attempt')
        if reconcile_only:
            if not old or old['phase'] in ('PREPARED', 'NO_INPUT'):
                raise bridge.TaskPackContractError('NO_SUBMITTED_ATTEMPT: cannot manufacture receipt')
            screen = bridge.read_screen(pack['callback_target'], lines=confirm_lines)
            observation = {
                'recorded_at_epoch': time.time(), 'screen': screen,
                'screen_sha256': bridge.screen_hash(screen), 'input_operations': 0,
            }
            observed = journal / ('observation-' + str(time.time_ns()) + '.json')
            write_json(observed, observation)
            bridge.require_agent_input(screen, pack['callback_target'])
            before = next(event['screen'] for event in old['events']
                          if event['phase'] == 'PASTE_INTENT')
            # Marker plus unrelated activity cannot reconcile a callback. Require
            # its whole exact line (allow terminal wrapping), actual transcript
            # activity after the marker, and no pending compose/queue copy.
            full_line = ''.join(pack['completion_callback'].split())
            confirmed = (
                full_line in ''.join(screen.split())
                and bridge._delivery_confirmed(before, screen, pack['completion_nonce'],
                                               pack['completion_callback'])
            )
            if not confirmed:
                raise bridge.DispatchUnconfirmed(
                    'CALLBACK_NOT_YET_CONFIRMED: read-only observation saved; do not resend',
                    state=bridge.DELIVERY_UNVERIFIED_BY_DETECTOR)
            result = {'confirmed': True, 'retries': old.get('extra_enter', 0)}
            evidence = {'reconciled_read_only': True, 'observation': str(observed),
                        'attempt': str(attempts[-1])}
        else:
            if old and old['phase'] != 'NO_INPUT':
                raise bridge.TaskPackContractError(
                    'CALLBACK_ATTEMPT_EXISTS: use --reconcile-only; never repaste uncertain delivery')
            # One explicit retry is allowed only after recorded zero input and
            # a fresh identity/composer check. Persistent failure becomes local
            # work, not an endless transport retry loop.
            if len(attempts) >= 2:
                raise bridge.TaskPackContractError('CALLBACK_RETRY_BUDGET_EXHAUSTED: supervisor must review')
            attempt_path = journal / f'attempt-{len(attempts)+1:04d}.json'
            attempt = {'binding': binding, 'phase': 'PREPARED', 'events': [],
                       'started_at_epoch': time.time()}
            write_json(attempt_path, attempt)

            def observe(phase, screen=None):
                attempt['phase'] = phase
                event = {'phase': phase, 'at_epoch': time.time()}
                if screen is not None:
                    event.update(screen=screen, screen_sha256=bridge.screen_hash(screen))
                attempt['events'].append(event)
                if phase == 'EXTRA_ENTER_INTENT':
                    attempt['extra_enter'] = 1
                write_json(attempt_path, attempt)

            try:
                result = bridge.submit_text(
                    pack['callback_target'], pack['completion_callback'],
                    marker=pack['completion_nonce'], confirm_lines=confirm_lines,
                    delivery_observer=observe)
                if result.get('confirmed') is not True:
                    raise bridge.DispatchUnconfirmed('callback not confirmed')
            except BaseException as exc:
                if attempt['phase'] == 'PREPARED':
                    attempt['phase'] = 'NO_INPUT'
                attempt.update(error=str(exc), delivery_state=getattr(exc, 'state', None),
                               ended_at_epoch=time.time())
                write_json(attempt_path, attempt)
                raise
            attempt.update(phase='CONFIRMED', result=result, ended_at_epoch=time.time())
            write_json(attempt_path, attempt)
            evidence = {'reconciled_read_only': False, 'attempt': str(attempt_path)}
        # The report must remain the exact document whose callback was sent.
        if bridge._sha256_file(report) != binding['report_sha256']:
            raise bridge.TaskPackContractError('REPORT_CHANGED_DURING_CALLBACK: receipt refused')
        receipt = {**{k: v for k, v in binding.items() if k != 'identity'},
                   'confirmed': True, 'bridge_retries': result.get('retries', 0),
                   'recorded_at_epoch': time.time(), **evidence}
        encoded = (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode()
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return receipt
