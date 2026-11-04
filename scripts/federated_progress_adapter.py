#!/usr/bin/env python3
"""Project pinned federation actors into the existing bounded progress observer.

No second scheduler, participant registry, transport, enrollment or acceptance.
Run with Python -B, including callers importing this module.
"""
import sys

sys.dont_write_bytecode = True

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import time

import collaboration_progress as progress
import federated_collaboration as federation


class AdapterError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise AdapterError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(manifest_path, local_host):
    """No runtime reads. Missing pins remain explicit rows, never fabricated."""
    manifest_path = Path(manifest_path)
    require(manifest_path.is_absolute(), 'ABSOLUTE_FEDERATION_REQUIRED')
    require(isinstance(local_host, str) and local_host.strip(), 'EXPLICIT_LOCAL_HOST_REQUIRED')
    data, declaration = federation.read_json(manifest_path)
    federation.validate(declaration, manifest_path.parent)
    pinned = sha(data)
    rows, tasks, inputs = [], [], {str(manifest_path.resolve())}
    for task in declaration['tasks']:
        for name in ('mode_ref', 'budget_ref', 'registration_ref'):
            if name in task:
                inputs.add(str((manifest_path.parent / task[name]['path']).resolve()))
    for actor in declaration['actors']:
        binding, identity = federation.reference(actor['binding_ref'], manifest_path.parent)
        inputs.add(identity[0])
        memberships = [t for t in declaration['tasks'] if actor['actor_id'] in t['participants']]
        alias = 'observe-' + sha(actor['actor_id'].encode())[:24]
        row = {'actor_id': actor['actor_id'], 'role': actor['role'],
               'binding_ref': copy.deepcopy(actor['binding_ref']),
               'native_session_id': binding['native_session_id'],
               'endpoint': copy.deepcopy(binding['endpoint']),
               'business_tasks': [{'task_id': t['task_id'], 'controller': t['controller']}
                                  for t in memberships],
               'observation_task_id': None, 'coverage': 'UNKNOWN',
               'accepted': False, 'task_completed': False}
        rows.append(row)
        if binding['endpoint']['host_identity'] != local_host:
            row['reason'] = 'REMOTE_HOST_NOT_OBSERVED'
            continue
        if 'process' not in binding:
            row['reason'] = 'ORIGINAL_PROCESS_PIN_MISSING'
            continue
        task = {'task_id': alias, 'role': actor['role'], 'process': copy.deepcopy(binding['process'])}
        if 'checkpoint' in binding:
            task['checkpoint'] = copy.deepcopy(binding['checkpoint'])
        if binding['endpoint']['transport'] == 'herdr':
            task['herdr'] = {
                'selector': {'kind': 'session', 'name': binding['endpoint']['session_selector']},
                'target': copy.deepcopy(binding)}
        try:
            progress.validate_manifest({'schema': progress.SCHEMA, 'new_tasks_only': True,
                'project_id': 'federation-observation', 'tasks': [task]})
        except (ValueError, OSError) as exc:
            row.update(reason='INVALID_ORIGINAL_OBSERVATION_PIN', error_code=str(exc))
            continue
        if 'checkpoint' in task:
            inputs.add(str(Path(task['checkpoint']['path']).resolve()))
        tasks.append(task)
        row.update(observation_task_id=alias, reason='AWAITING_ORIGINAL_OBSERVER')
    require(len(tasks) <= progress.MAX_TASKS, 'ORIGINAL_OBSERVER_ACTOR_LIMIT_EXCEEDED')
    # This is a NEW read-only observation assignment, not a claim that existing
    # business tasks are new, registered, dispatched or migrated.
    derived = {'schema': progress.SCHEMA, 'new_tasks_only': True,
               'project_id': 'federation-observe-' + sha(declaration['project_id'].encode())[:24],
               'tasks': tasks}
    if tasks:
        progress.validate_manifest(derived)
    require(sha(federation.read_json(manifest_path)[0]) == pinned, 'FEDERATION_CHANGED_DURING_PREPARATION')
    return derived, {'schema': 'federated-progress-map-v1',
        'project_id': declaration['project_id'], 'manifest_path': str(manifest_path),
        'manifest_sha256': pinned, 'local_host_selection': local_host,
        'selection_is_host_authentication': False,
        'source': 'new_read_only_observation_assignment_not_business_registration',
        'actor_count': len(rows), 'eligible_actor_count': len(tasks),
        'coverage_sufficient': len(tasks) == len(rows), 'actors': rows,
        'input_paths': sorted(inputs), 'registered': False, 'input_sent': False,
        'accepted': False, 'migrate': False}


class Evidence:
    """Record exact read outcomes; scheduling/UNKNOWN decisions stay upstream."""
    def __init__(self, output, *, process_reader, herdr_reader, wall):
        self.output, self.wall = output, wall
        self.process_reader, self.herdr_reader = process_reader, herdr_reader
        self.records = {'process': {}, 'herdr': {}}

    def read(self, kind, key, call):
        row = {'started_at': progress.utc(self.wall()), 'state': 'UNKNOWN'}
        try:
            result = call()
            row.update(state='READ', result=copy.deepcopy(result))
            return result
        except Exception as exc:
            row.update(error_type=type(exc).__name__, error_code=str(exc)[:2048])
            raise
        finally:
            row['observed_at'] = progress.utc(self.wall())
            self.records[kind][key] = row
            self.output.write('native-read-evidence.json', self.records)

    def process(self, pid, *, timeout):
        return self.read('process', str(pid), lambda: self.process_reader(pid, timeout=timeout))

    def herdr(self, binding, *, timeout):
        key = binding['target']['native_session_id']
        return self.read('herdr', key, lambda: self.herdr_reader(binding, timeout=timeout))


def project(mapping, snapshot, observer, evidence, *, now):
    """An all-actor historical view, including those the observer cannot read."""
    samples = {t['task_id']: t for t in snapshot.get('tasks', [])} if snapshot else {}
    valid_until = snapshot.get('coverage_valid_until') if snapshot else None
    fresh = bool(valid_until and now < progress.epoch(valid_until))
    rows = []
    for original in mapping['actors']:
        row = copy.deepcopy(original)
        sample = samples.get(row['observation_task_id'])
        row.update(runtime_state='UNKNOWN', native_identity_verified=False,
                   sample_fresh=False, observed_at=None)
        if sample is not None:
            row['original_observation'] = sample
            row['sample_fresh'] = fresh
            row['observed_at'] = sample['handle']['observed_at']
            row['reason'] = 'ORIGINAL_OBSERVER_UNKNOWN'
            if fresh:
                if sample['herdr'].get('identity_matches') is True:
                    row.update(runtime_state=sample['herdr']['state'],
                               native_identity_verified=True, reason='HERDR_IDENTITY_CHECKED')
                    if row['runtime_state'].upper() in ('UNKNOWN', 'NOT_CONFIGURED'):
                        row.update(runtime_state='UNKNOWN', reason='HERDR_RUNTIME_UNKNOWN')
                elif row['endpoint']['transport'] == 'cmux':
                    row.update(runtime_state=('PROCESS_' + sample['handle']['state']
                                              if sample['handle']['state'] != 'UNKNOWN' else 'UNKNOWN'),
                               reason='PROCESS_ONLY_NATIVE_SESSION_NOT_RECHECKED')
            else:
                row['reason'] = 'SNAPSHOT_STALE'
            row['coverage'] = 'OBSERVED' if fresh and (
                sample['handle']['state'] != 'UNKNOWN' and
                row['runtime_state'] != 'UNKNOWN' and
                (row['endpoint']['transport'] == 'cmux' or row['native_identity_verified'])) else 'UNKNOWN'
            native = evidence.get('herdr', {}).get(row['native_session_id'])
            if native:
                row['native_read_evidence'] = native
            process_pin = sample['handle']['pinned']
            process = evidence.get('process', {}).get(str(process_pin['pid']))
            if process:
                row['process_read_evidence'] = process
        rows.append(row)
    return {'schema': 'federated-progress-snapshot-v1', 'project_id': mapping['project_id'],
            'manifest_sha256': mapping['manifest_sha256'], 'projected_at': progress.utc(now),
            'original_observed_at': snapshot.get('observed_at') if snapshot else None,
            'coverage_valid_until': valid_until, 'actors': rows,
            'actor_count': len(rows), 'unknown_actor_count': sum(r['coverage'] == 'UNKNOWN' for r in rows),
            'active_monitoring_coverage': observer.get('coverage', 'UNKNOWN'),
            'observer_status': observer.get('status', 'NOT_STARTED'),
            'native_notification': False, 'registered': False, 'input_sent': False,
            'accepted': False, 'task_completed': False, 'migrate': False}


def run(manifest_path, output_path, *, local_host, mode, duration=None,
        process_reader=progress.native_process, herdr_reader=progress.native_herdr,
        wall=time.time, monotonic=time.monotonic, stop=None, waiter=None):
    require(mode in ('snapshot', 'watch'), 'INVALID_MODE')
    require((mode == 'snapshot' and duration is None) or
            (mode == 'watch' and type(duration) is int and progress.CADENCE <= duration <= 86400),
            'BOUNDED_DURATION_REQUIRED')
    derived, mapping = prepare(manifest_path, local_host)
    output_path = Path(output_path)
    require(output_path.is_absolute(), 'ABSOLUTE_OUTPUT_REQUIRED')
    destination = output_path.resolve()
    require(all(destination != Path(p) and destination not in Path(p).parents
                for p in mapping['input_paths']), 'INPUT_INSIDE_ADAPTER_OUTPUT')
    with progress.Output(output_path) as output:
        require(not any(p.name != '.writer.lock' for p in output_path.iterdir()), 'OUTPUT_ALREADY_USED')
        output.write('actor-map.json', mapping)
        output.write('progress-input.json', derived)
        evidence = Evidence(output, process_reader=process_reader, herdr_reader=herdr_reader, wall=wall)
        observer, snapshot = {}, None
        if derived['tasks']:
            observer = progress.run(output_path / 'progress-input.json', output_path / 'progress',
                mode=mode, duration=duration, process_reader=evidence.process, herdr_reader=evidence.herdr,
                wall=wall, monotonic=monotonic, stop=stop, waiter=waiter)
            snapshot = progress.decode(progress.read_regular(output_path/'progress'/'snapshot.json',
                                                              progress.MAX_JOURNAL)[0])
        view = project(mapping, snapshot, observer, evidence.records, now=wall())
        output.write('federation-snapshot.json', view)
        return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'snapshot', 'watch'):
        child = commands.add_parser(name)
        child.add_argument('--manifest', required=True, type=Path)
        child.add_argument('--local-host', required=True)
        if name != 'prepare':
            child.add_argument('--output', required=True, type=Path)
        if name == 'watch':
            child.add_argument('--duration-seconds', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            derived, mapping = prepare(args.manifest, args.local_host)
            result = {'progress_manifest': derived, 'actor_map': mapping}
        else:
            result = run(args.manifest, args.output, local_host=args.local_host,
                         mode=args.command, duration=getattr(args, 'duration_seconds', None))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if args.command == 'prepare':
            return 0 if result['actor_map']['coverage_sufficient'] else 2
        return 0 if result['unknown_actor_count'] == 0 else 2
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error_type': type(exc).__name__, 'error_code': str(exc),
                          'registered': False, 'input_sent': False, 'accepted': False}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
