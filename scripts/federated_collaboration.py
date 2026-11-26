#!/usr/bin/env python3
"""Read-only consistency checks for referenced federation declarations.

This is not a participant registry, live identity check, sender, or lease guard.
"""
import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import stat
import uuid

from collaboration_profile import select_profile


class FederationError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise FederationError(reason)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def record(value, required, optional=()):
    require(isinstance(value, dict), 'OBJECT_REQUIRED')
    require(set(required) <= value.keys(), 'MISSING_FIELDS:' + ','.join(required))
    require(value.keys() <= set(required) | set(optional), 'UNKNOWN_FIELDS')


def strings(values):
    require(isinstance(values, list) and values and all(text(x) for x in values),
            'NONEMPTY_STRING_LIST_REQUIRED')
    require(len(values) == len(set(values)), 'DUPLICATE_REFERENCE')


def unique(value, seen, reason):
    require(value not in seen, reason)
    seen.add(value)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def read_json(path):
    require(stat.S_ISREG(path.stat().st_mode), 'REGULAR_FILE_REQUIRED')
    with path.open('rb') as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    require(len(data) <= 2 * 1024 * 1024, 'DECLARATION_TOO_LARGE')
    return data, json.loads(data.decode('utf-8'), object_pairs_hook=strict_object)


def reference(ref, base):
    record(ref, ('path', 'sha256', 'pointer'))
    require(text(ref['path']) and isinstance(ref['pointer'], str), 'INVALID_REFERENCE')
    require(isinstance(ref['sha256'], str) and
            re.fullmatch('[0-9a-f]{64}', ref['sha256']), 'INVALID_REFERENCE_SHA256')
    path = (base / ref['path']).resolve()
    data, value = read_json(path)
    require(hashlib.sha256(data).hexdigest() == ref['sha256'], 'REFERENCE_HASH_MISMATCH')
    pointer = ref['pointer']
    require(pointer == '' or pointer.startswith('/'), 'INVALID_JSON_POINTER')
    for part in pointer.split('/')[1:] if pointer else ():
        require(re.search(r'~(?![01])', part) is None, 'INVALID_JSON_POINTER')
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict):
            require(part in value, 'REFERENCE_POINTER_MISSING')
            value = value[part]
        elif isinstance(value, list):
            require(re.fullmatch(r'0|[1-9][0-9]*', part) is not None and
                    int(part) < len(value), 'REFERENCE_POINTER_MISSING')
            value = value[int(part)]
        else:
            raise FederationError('REFERENCE_POINTER_MISSING')
    return value, (str(path), ref['sha256'], pointer)


def service_key(service):
    record(service, ('host_identity', 'session_selector', 'socket'))
    require(all(text(x) for x in service.values()), 'INVALID_SERVICE')
    require(Path(service['socket']).is_absolute(), 'ABSOLUTE_SOCKET_REQUIRED')
    require(not service['session_selector'].startswith('-') and
            not any(c in service['session_selector'] for c in '\0\r\n'), 'INVALID_SELECTOR')
    return (service['host_identity'], posixpath.normpath(service['socket']))


def workspace_uuid(value):
    require(text(value), 'INVALID_CMUX_UUID')
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise FederationError('INVALID_CMUX_UUID') from exc


def binding(value):
    # An existing target may have further original evidence; do not rewrite it.
    require(isinstance(value, dict), 'INVALID_BINDING')
    require(all(text(value.get(k)) for k in ('agent', 'native_session_id', 'terminal_id')),
            'INCOMPLETE_NATIVE_BINDING')
    outer = value.get('outer_cmux')
    require(isinstance(outer, dict) and all(text(outer.get(k)) for k in
            ('workspace_uuid', 'pane_uuid', 'surface_uuid')), 'INCOMPLETE_OUTER_BINDING')
    for key in ('workspace_uuid', 'pane_uuid', 'surface_uuid'):
        workspace_uuid(outer[key])
    endpoint = value.get('endpoint')
    require(isinstance(endpoint, dict) and endpoint.get('transport') in ('cmux', 'herdr'),
            'INVALID_ENDPOINT')
    require(text(endpoint.get('host_identity')), 'HOST_IDENTITY_REQUIRED')
    if endpoint['transport'] == 'herdr':
        require(text(value.get('pane_id')), 'INNER_PANE_REQUIRED')
        service_key({k: endpoint.get(k) for k in ('host_identity', 'session_selector', 'socket')})
    return value


def validate(manifest, base):
    record(manifest, ('schema', 'project_id', 'senior', 'actors', 'chapters', 'tasks'))
    require(manifest['schema'] == 'federated-collaboration-v1' and
            text(manifest['project_id']), 'INVALID_FEDERATION')
    for field in ('actors', 'chapters', 'tasks'):
        require(isinstance(manifest[field], list) and manifest[field], 'EMPTY_' + field.upper())
    actors, native_ids, terminals, inner_panes = {}, set(), set(), set()
    for actor in manifest['actors']:
        record(actor, ('actor_id', 'role', 'binding_ref'))
        require(text(actor['actor_id']) and text(actor['role']), 'INVALID_ACTOR')
        require(actor['actor_id'] not in actors, 'DUPLICATE_ACTOR_ALIAS')
        original, _ = reference(actor['binding_ref'], base)
        target = binding(original)
        unique((target['agent'], target['native_session_id']), native_ids, 'DUPLICATE_NATIVE_ACTOR')
        unique((target['endpoint']['host_identity'], target['terminal_id']), terminals,
               'DUPLICATE_NATIVE_TERMINAL')
        endpoint = target['endpoint']
        if endpoint['transport'] == 'herdr':
            unique((endpoint['host_identity'], posixpath.normpath(endpoint['socket']),
                    target['pane_id']), inner_panes, 'DUPLICATE_INNER_PANE')
        actors[actor['actor_id']] = target
    senior = manifest['senior']
    require(isinstance(senior, str) and senior in actors, 'UNKNOWN_SENIOR')
    chapters, membership, supervisors = {}, {}, set()
    # Chapter workspaces are exclusive among chapters. An existing dedicated
    # senior terminal need not acquire a fifth workspace merely to validate.
    workspaces = set()
    services, selectors = set(), set()
    for chapter in manifest['chapters']:
        record(chapter, ('chapter_id', 'workspace_uuid', 'herdr_service', 'supervisor', 'participants'))
        require(text(chapter['chapter_id']) and text(chapter['workspace_uuid']), 'INVALID_CHAPTER')
        require(chapter['chapter_id'] not in chapters, 'DUPLICATE_CHAPTER')
        unique(workspace_uuid(chapter['workspace_uuid']), workspaces, 'DUPLICATE_WORKSPACE')
        key = service_key(chapter['herdr_service'])
        unique(key, services, 'DUPLICATE_SERVICE_SOCKET')
        unique((key[0], chapter['herdr_service']['session_selector']), selectors,
               'DUPLICATE_SERVICE_SELECTOR')
        strings(chapter['participants'])
        require(isinstance(chapter['supervisor'], str) and
                chapter['supervisor'] in chapter['participants'], 'SUPERVISOR_NOT_PARTICIPANT')
        for actor_id in chapter['participants']:
            require(actor_id in actors and actor_id != senior, 'INVALID_CHAPTER_ACTOR')
            require(actor_id not in membership, 'ACTOR_IN_MULTIPLE_CHAPTERS')
            target = actors[actor_id]
            require(workspace_uuid(target['outer_cmux']['workspace_uuid']) ==
                    workspace_uuid(chapter['workspace_uuid']),
                    'ACTOR_WORKSPACE_MISMATCH')
            endpoint = target['endpoint']
            require(endpoint['transport'] == 'herdr' and all(
                endpoint.get(k) == v for k, v in chapter['herdr_service'].items()),
                'ACTOR_SERVICE_MISMATCH')
            membership[actor_id] = chapter['chapter_id']
        supervisors.add(chapter['supervisor'])
        chapters[chapter['chapter_id']] = chapter
    require(set(actors) == set(membership) | {senior}, 'UNASSIGNED_ACTOR')
    tasks, lineage_refs, scope_refs, routes = {}, {}, {}, []
    for task in manifest['tasks']:
        record(task, ('task_id', 'parent_task_id', 'chapter_id', 'owner', 'participants',
                      'controller', 'mode_ref', 'budget_ref', 'routes'), ('registration_ref',))
        require(text(task['task_id']) and task['task_id'] not in tasks, 'DUPLICATE_OR_INVALID_TASK')
        require(task['parent_task_id'] is None or text(task['parent_task_id']), 'INVALID_PARENT')
        require(task['chapter_id'] is None or (isinstance(task['chapter_id'], str) and
                task['chapter_id'] in chapters), 'UNKNOWN_CHAPTER')
        strings(task['participants'])
        require(set(task['participants']) <= actors.keys(), 'UNKNOWN_PARTICIPANT')
        require(isinstance(task['owner'], str) and task['owner'] in task['participants'],
                'OWNER_NOT_PARTICIPANT')
        require(text(task['controller']) or (isinstance(task['controller'], dict) and
                bool(task['controller'])), 'CONTROLLER_REQUIRED')
        if task['chapter_id'] is None:
            require(task['owner'] == senior and
                    set(task['participants']) <= supervisors | {senior}, 'FEDERATION_OWNER_OR_SCOPE')
        else:
            chapter = chapters[task['chapter_id']]
            require(set(task['participants']) <= set(chapter['participants']), 'CHAPTER_SCOPE_MISMATCH')
        mode, _ = reference(task['mode_ref'], base)
        record(mode, ('name', 'revision'))
        require(mode['name'] in ('peer', 'host_dispatch', 'hierarchical', 'closed_loop') and
                text(mode['revision']), 'INVALID_MODE')
        budget, budget_key = reference(task['budget_ref'], base)
        record(budget, ('issue_lineage_id', 'acceptance_scope_id', 'max_rounds', 'rounds_used',
                       'time_limit_seconds', 'token_limit'))
        require(text(budget['issue_lineage_id']) and text(budget['acceptance_scope_id']),
                'INVALID_ISSUE_LINEAGE_OR_SCOPE')
        require(all(type(budget[k]) is int for k in ('max_rounds', 'rounds_used',
                'time_limit_seconds', 'token_limit')), 'INTEGER_BUDGET_REQUIRED')
        require(1 <= budget['max_rounds'] <= 3 and
                0 <= budget['rounds_used'] <= budget['max_rounds'] and
                budget['time_limit_seconds'] > 0 and budget['token_limit'] > 0,
                'INVALID_CORRECTION_BUDGET')
        lineage = budget['issue_lineage_id']
        require(lineage not in lineage_refs or lineage_refs[lineage] == budget_key,
                'ISSUE_LINEAGE_BUDGET_FORK')
        lineage_refs[lineage] = budget_key
        scope = budget['acceptance_scope_id']
        require(scope not in scope_refs or scope_refs[scope] == budget_key,
                'ACCEPTANCE_SCOPE_BUDGET_FORK')
        scope_refs[scope] = budget_key
        reg = None
        if 'registration_ref' in task:
            reg, _ = reference(task['registration_ref'], base)
            require(isinstance(reg, dict) and reg.get('task_id') == task['task_id'],
                    'ORIGINAL_TASK_MISMATCH')
            require('controller' in reg and reg['controller'] == task['controller'],
                    'ORIGINAL_CONTROLLER_CHANGED')
        require(isinstance(task['routes'], list) and task['routes'], 'NONEMPTY_ROUTES_REQUIRED')
        edges = set()
        for route in task['routes']:
            record(route, ('sender', 'recipient', 'coordination_scope', 'delivery_transport'))
            require(all(isinstance(route[k], str) and route[k] in task['participants']
                        for k in ('sender', 'recipient')), 'ROUTE_OUTSIDE_PARTICIPANTS')
            require(route['sender'] != route['recipient'], 'SELF_ROUTE')
            unique((route['sender'], route['recipient']), edges, 'DUPLICATE_ROUTE_EDGE')
            require(route['coordination_scope'] in ('chapter', 'federation') and
                    route['delivery_transport'] in ('herdr', 'cmux', 'original'), 'INVALID_ROUTE')
            scope = 'federation' if task['chapter_id'] is None else 'chapter'
            require(route['coordination_scope'] == scope, 'COORDINATION_SCOPE_MISMATCH')
            target = actors[route['recipient']]
            requested = route['delivery_transport']
            if reg is not None and reg.get('schema') != 'collaboration-profile-v1':
                require(requested == 'original', 'LEGACY_ROUTE_REQUIRES_ORIGINAL')
                selection = select_profile(reg, {})
            else:
                require(requested == target['endpoint']['transport'], 'DELIVERY_ENDPOINT_MISMATCH')
                selection = select_profile(reg, {}, target=target, requested_profile=requested)
                require('conflict' not in selection, 'REGISTERED_ROUTE_CONFLICT')
            routes.append({'task_id': task['task_id'], 'sender': route['sender'],
                           'recipient': route['recipient'], 'coordination_scope': scope,
                           'profile': selection['profile'], 'controller': task['controller'],
                           'migrate': False})
        tasks[task['task_id']] = task
    roots = [t for t in tasks.values() if t['parent_task_id'] is None]
    require(len(roots) == 1 and roots[0]['chapter_id'] is None, 'ONE_PROJECT_ROOT_REQUIRED')
    for task in tasks.values():
        parent_id = task['parent_task_id']
        if parent_id is not None:
            require(parent_id in tasks, 'UNKNOWN_PARENT_TASK')
            parent = tasks[parent_id]
            require(parent['chapter_id'] in (None, task['chapter_id']), 'CROSS_CHAPTER_PARENT')
            if task['chapter_id'] is not None and parent['chapter_id'] is None:
                require(task['owner'] == chapters[task['chapter_id']]['supervisor'],
                        'CHAPTER_ROOT_OWNER_MISMATCH')
        seen = {task['task_id']}
        while parent_id is not None:
            require(parent_id in tasks, 'UNKNOWN_PARENT_TASK')
            unique(parent_id, seen, 'TASK_TREE_CYCLE')
            parent_id = tasks[parent_id]['parent_task_id']
    return {'declaration_consistent': True, 'chapter_count': len(chapters),
            'actor_count': len(actors), 'task_count': len(tasks), 'routes': routes,
            'live_identity_verified': False, 'registered': False, 'input_sent': False,
            'accepted': False, 'resource_exclusion_verified': False, 'migrate': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args(argv)
    try:
        _, manifest = read_json(args.manifest)
        result = validate(manifest, args.manifest.resolve().parent)
    except (FederationError, OSError, ValueError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, FederationError) else type(exc).__name__
        print(json.dumps({'declaration_consistent': False, 'error': reason,
                          'registered': False, 'input_sent': False, 'accepted': False,
                          'migrate': False}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
