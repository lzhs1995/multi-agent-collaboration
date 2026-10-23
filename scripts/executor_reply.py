"""Exact reply contracts shared by one-shot idle observation and durable reasks.

A matching reply ends a waiting episode. It never proves delivery of the
original request, dispatch of a task pack, or business acceptance.
"""
import json

REPLY_STATES = {'ACK', 'TASK', 'DISPATCHED', 'BLOCKED', 'WAITING_DEPENDENCY', 'SOLO'}
PREFIX = 'EXECUTOR_REPLY|'


def rejection(body, markers, *, caller, task_id, episode_id, supervisor='', template_only=False):
    """Explain validation without rewriting the reply or accepting new statuses."""
    if not isinstance(body, dict):
        return 'REPLY_OBJECT_REQUIRED'
    if not episode_id or not markers:
        return 'ORIGINAL_EPISODE_AND_MARKER_REQUIRED'
    if body.get('marker') not in markers:
        return 'REPLY_MARKER_MISMATCH'
    for key, expected in (('caller_surface_uuid', caller), ('task_id', task_id),
                          ('episode_id', episode_id)):
        if body.get(key) != expected:
            return 'REPLY_BINDING_MISMATCH:' + key
    if not isinstance(body.get('status'), str) or body['status'] not in REPLY_STATES:
        return 'REPLY_STATUS_UNSUPPORTED'
    if body.get('queued') is True or body.get('received') is False:
        return 'QUEUED_OR_UNRECEIVED_REPLY'
    if supervisor and body.get('supervisor_uuid') != supervisor:
        return 'REPLY_BINDING_MISMATCH:supervisor_uuid'
    if body['status'] in {'WAITING_DEPENDENCY', 'SOLO', 'BLOCKED'}:
        trigger = body.get('trigger')
        if (not isinstance(trigger, str) or not trigger.strip()
                or not template_only and trigger.strip() == 'REPLACE_WITH_CONCRETE_TRIGGER'):
            return 'CONCRETE_TRIGGER_REQUIRED'
    return None


def valid(body, markers, *, caller, task_id, episode_id, supervisor='', template_only=False):
    return rejection(body, markers, caller=caller, task_id=task_id,
                     episode_id=episode_id, supervisor=supervisor,
                     template_only=template_only) is None


def from_text(text):
    """Only a complete structured reply, never a marker citation or status prose."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith(PREFIX):
        text = text[len(PREFIX):]
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def template(state, marker):
    value = dict(marker=marker, caller_surface_uuid=state['caller_surface_uuid'],
                supervisor_uuid=state['supervisor'], task_id=state.get('task_id', ''),
                episode_id=state['episode_id'], status='WAITING_DEPENDENCY',
                trigger='REPLACE_WITH_CONCRETE_TRIGGER')
    if state.get('continuation'):
        due = state.get('next_due_epoch', state['started_epoch'])
        value.update(owner=state['supervisor'], next_due_epoch=due, deadline_epoch=due,
                     overall_task=state['continuation']['overall_task'])
    return value
