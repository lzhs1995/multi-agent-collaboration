"""One resolved caller per hook evaluation, without rewriting process environment."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import subprocess

import cmux_workspace_guard as workspace

_current = ContextVar('cmux_hook_identity', default=None)


def resolve(payload):
    if workspace.daemon_identity.collect(os.environ) is not None:
        # Use the same authenticated native caller as transport and marker writes.
        _identity, _tree, env, proof = workspace.caller_snapshot()
        if proof is None:
            workspace.deny('managed hook caller disappeared during resolution')
        ws, surface = env.get('CMUX_WORKSPACE_ID'), env.get('CMUX_SURFACE_ID')
        if not ws or not surface:
            workspace.deny('resolved hook caller lacks workspace or surface')
        return ws, surface
    return (os.environ.get('CMUX_WORKSPACE_ID') or payload.get('workspace_id') or 'default',
            os.environ.get('CMUX_SURFACE_ID') or payload.get('surface_id')
            or payload.get('surface_uuid'))


def identity(payload):
    value = _current.get()
    return value if value is not None else resolve(payload)


@contextmanager
def evaluation(payload):
    token = _current.set(resolve(payload))
    try:
        yield
    finally:
        _current.reset(token)


ERRORS = (workspace.WorkspaceScopeError, workspace.daemon_identity.IdentityError,
          OSError, ValueError, subprocess.SubprocessError)
