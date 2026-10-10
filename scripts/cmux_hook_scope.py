"""Task hooks apply only to explicitly enrolled native sessions.

An installed skill, a workspace, a surface reused by a new session, and another
task's marker are not enrollment. Scope selection is not delivery authentication:
an enrolled caller still goes through the unchanged native identity resolver.
"""
from contextlib import contextmanager
from contextvars import ContextVar

import cmux_hook_identity as identity

_selected = ContextVar('cmux_hook_task_scope', default=None)


def session_id(payload):
    value = payload.get('session_id') or payload.get('sessionId')
    return value if isinstance(value, str) and value else None


def participants(marker, payload):
    rows = marker.get('participants', [])
    if not isinstance(rows, list):
        return []
    session = session_id(payload)
    result = []
    for row in rows:
        if not isinstance(row, dict) or not row.get('surface_uuid'):
            continue
        if row.get('role') != 'supervisor' and not str(row.get('role', '')).startswith('executor'):
            continue
        pinned = row.get('native_session_id')
        # Native events require a native session enrollment. Unpinned legacy
        # markers remain evidence, but cannot enroll a later/new native session.
        if session is not None:
            if pinned != session:
                continue
        elif pinned:
            # Missing native identity cannot inherit a session-bound task.
            continue
        result.append(row)
    return result


def applies(marker, payload, workspace, surface):
    if marker.get('_scope_workspace', workspace) != workspace:
        return False
    recorded = marker.get('workspace_uuid') or marker.get('workspace_id') or marker.get('_scope_workspace')
    if recorded and recorded != workspace:
        return False
    return any(row['surface_uuid'] == surface for row in participants(marker, payload))


def current():
    return _selected.get()


@contextmanager
def evaluation(payload, markers):
    candidates = [marker for marker in markers if participants(marker, payload)]
    if not candidates:
        yield []
        return
    # Do not catch an exception raised by the consumer of this context manager.
    # An unresolved legacy surface is not proven jurisdiction. A native session
    # explicitly enrolled in this task must still authenticate before proceeding.
    context = identity.evaluation(payload)
    try:
        context.__enter__()
    except identity.ERRORS:
        if session_id(payload) is not None:
            raise
        yield []
        return
    try:
        workspace, surface = identity.identity(payload)
        selected = [m for m in candidates if applies(m, payload, workspace, surface)]
        token = _selected.set(selected)
        try:
            yield selected
        finally:
            _selected.reset(token)
    finally:
        context.__exit__(None, None, None)


def bind_participants(bridge, workspace, rows):
    """Read the designated live native sessions once before arming a new task.

    No input, no latest-file guessing, and no implicit replacement session.
    The transport's existing kernel/TTY/session readers perform the binding.
    """
    import cmux_native_delivery as native
    bound = []
    for row in rows:
        target = native._tree_row(bridge, row['surface_uuid'])
        if target['workspace_uuid'] != workspace:
            raise native.NativeDeliveryError('TASK_SCOPE_WORKSPACE_MISMATCH')
        process = native._target_process(target)
        provider = native._provider(process)
        if row.get('provider') and row['provider'] != provider:
            raise native.NativeDeliveryError('TASK_SCOPE_PROVIDER_MISMATCH')
        session = native._live_session(process, provider)
        if native.identity.process(process['pid']) != process:
            raise native.NativeDeliveryError('TASK_SCOPE_PROCESS_CHANGED')
        bound.append(dict(row, native_session_id=session))
    return bound
