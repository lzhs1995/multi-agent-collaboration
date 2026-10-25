"""Prepare a pinned ordinary completion notice for an issued scoped work order.

This command sends no input and grants no task, acceptance, callback or release
authority. Formal harness packs keep submit_completion_callback; their reports
cannot be disguised as scoped ordinary notifications here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

from cmux_evidence_io import snapshot

MAX_WORK_ORDER_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024


def _pin(path, expected, limit):
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('exact SHA256 required')
    raw, identity = snapshot(Path(path), limit=limit)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected:
        raise ValueError('pinned content changed')
    return raw, dict(path=str(path), sha256=digest, bytes=len(raw), identity=list(identity))


def _output_path(order, outputs, stage, output_names, top_names=()):
    values = [outputs[name] for name in output_names if name in outputs]
    values.extend(order[name] for name in top_names if name in order)
    if not values or any(not isinstance(value, str) or not value for value in values):
        raise ValueError(stage + ' output path missing or invalid: ' + ', '.join(output_names))
    if len(set(values)) != 1:
        raise ValueError(stage + ' output path aliases disagree')
    return values[0]


def _controller_source(order):
    """Use issued controller pins; never select a new release via global links."""
    declared = [order[name] for name in ('controller', 'controller_source', 'completion_controller')
                if name in order]
    required = order.get('required_skill')
    if required is not None:
        if not isinstance(required, dict):
            raise ValueError('required_skill must contain the exact SKILL.md pin')
        skill_path = required.get('path')
        if not isinstance(skill_path, str) or not Path(skill_path).is_absolute():
            raise ValueError('required_skill needs an absolute SKILL.md path')
        declared.append(str(Path(skill_path).parent))
    if not declared:
        # Older scoped orders may omit the controller. They retain the helper
        # actually invoked, rather than acquiring whatever CURRENT selects.
        declared = [str(Path(__file__).resolve().parent.parent)]
    if any(not isinstance(value, str) or not value or not Path(value).is_absolute()
           or '..' in Path(value).parts or Path(value).resolve() != Path(value)
           for value in declared) or len(set(declared)) != 1:
        raise ValueError('issued controller aliases must agree on one canonical absolute source')
    source = Path(declared[0])
    if not source.is_dir() or source.is_symlink():
        raise ValueError('issued controller source is unavailable')
    skill = source / 'SKILL.md'
    raw, identity = snapshot(skill, limit=MAX_WORK_ORDER_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    if required is not None and (
            required.get('path') != str(skill) or required.get('sha256') != digest
            or type(required.get('bytes')) is not int or required['bytes'] != len(raw)):
        raise ValueError('issued required_skill pin changed or conflicts with controller')
    for name in ('cmux_bridge.py', 'cmux_prompt_reference.py', 'cmux_evidence_io.py'):
        member = source / 'scripts' / name
        if member.resolve() != member or member.is_symlink() or not member.is_file():
            raise ValueError('issued controller member unavailable: ' + name)
    return source, dict(path=str(skill), bytes=len(raw), sha256=digest,
                        identity=list(identity), issued_pin_verified=required is not None)


def plan(work_order, work_order_sha256, report, report_sha256):
    """Read both exact UTF-8 bodies once and describe the declared ordinary route."""
    work_raw, work_pin = _pin(work_order, work_order_sha256, MAX_WORK_ORDER_BYTES)
    report_raw, report_pin = _pin(report, report_sha256, MAX_REPORT_BYTES)
    order = json.loads(work_raw.decode('utf-8'))
    report_raw.decode('utf-8', errors='strict')
    if not isinstance(order, dict) or not isinstance(order.get('schema'), str):
        raise ValueError('issued scoped work order required')
    # A domain-specific work-order schema can explicitly declare the ordinary
    # result route. Keep the legacy scoped-schema fallback and reject actual
    # formal task/callback bindings regardless of that declaration.
    if (order.get('completion_callback') or order.get('formal_harness_receipt') is True
            or order.get('completion_nonce') or order.get('callback_nonce')
            or order.get('task_id')
            or ('scoped' not in order['schema']
                and order.get('completion_channel') != 'ORDINARY_NOTICE')):
        raise ValueError('formal harness task requires its original callback route')
    outputs = order.get('outputs', {})
    if not isinstance(outputs, dict):
        raise ValueError('issued output paths must be an object')
    declared = _output_path(order, outputs, 'final', ('final_output', 'final_path', 'report', 'final_report_path'),
                            ('final_report_path',))
    marker = order.get('done_marker')
    owner = order.get('supervisor_surface_uuid')
    try:
        owner_valid = isinstance(owner, str) and str(uuid.UUID(owner)).lower() == owner.lower()
    except (ValueError, AttributeError):
        owner_valid = False
    channel = order.get('completion_channel', 'ORDINARY_NOTICE')
    if (not isinstance(declared, str) or not Path(declared).is_absolute()
            or str(Path(declared)) != str(Path(report))
            or not isinstance(marker, str) or not re.fullmatch('[A-Za-z0-9_-]{1,160}', marker)
            or not owner_valid or channel != 'ORDINARY_NOTICE'):
        raise ValueError('final output, unique marker and original supervisor must be declared')
    work_id = order.get('work_id') or order.get('id')
    if not isinstance(work_id, str) or not work_id:
        raise ValueError('scoped work identifier missing')
    progress = _output_path(order, outputs, 'progress',
                            ('progress_path', 'progress', 'progress_output'), ('progress_path',))
    acceptance = _output_path(order, outputs, 'acceptance',
                              ('acceptance', 'acceptance_path', 'acceptance_output'), ('acceptance_path',))
    stages = [declared, progress, acceptance]
    if (any(not isinstance(p, str) or not Path(p).is_absolute()
            or '..' in Path(p).parts or Path(p).resolve() != Path(p) for p in stages)
            or len({str(Path(p)) for p in stages}) != 3):
        raise ValueError('acceptance, progress and final output must have separate absolute paths')
    # The public bridge only accepts bounded inline wire text. Plan the shared
    # reference representation without writing its body; persistence precedes
    # one authorized submission, never an existing unknown attempt.
    body = json.dumps(dict(kind='SCOPED_WORK_RESULT_NOTICE', marker=marker, work_id=work_id,
                          work_order={k: work_pin[k] for k in ('path', 'sha256', 'bytes')},
                          report={k: report_pin[k] for k in ('path', 'sha256', 'bytes')},
                          formal_completion_callback=False, business_accepted=False,
                          input_operations_by_planner=0), ensure_ascii=False, sort_keys=True)
    source, controller_skill = _controller_source(order)
    controller = source / 'scripts'
    from cmux_prompt_reference import plan as reference_plan
    prepared = reference_plan(body, marker)
    # Use the actual public CLI with bounded wire text as one argv item. Shell
    # quoting is for display only; execute submit_argv without interpolation.
    executable = str(Path(sys.executable).resolve())
    submit_argv = [executable, '-BE', str(controller / 'cmux_bridge.py'),
                   'submit-text', '--surface', owner, '--text', prepared['text'],
                   '--marker', marker, '--wait-seconds', '0']
    persist_argv = None
    ready_persist_argv = None
    if prepared['reference'] is not None:
        persistence = (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); "
            "from cmux_evidence_io import snapshot; "
            "from cmux_prompt_reference import persist; "
            "ref=json.loads(sys.argv[2]); "
            "raw,identity=snapshot(Path(sys.argv[3]),limit=1024*1024); "
            "print(json.dumps(persist(ref,raw.decode('utf-8')),ensure_ascii=False))"
        )
        persist_argv = [executable, '-BE', '-c', persistence, str(controller),
                        json.dumps(prepared['reference'], ensure_ascii=False),
                        '<new-exact-notice-body-file>']
        stdin_persistence = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); "
            "from cmux_prompt_reference import persist; "
            "ref=json.loads(sys.argv[2]); raw=sys.stdin.buffer.read(1024*1024+1); "
            "print(json.dumps(persist(ref,raw.decode('utf-8',errors='strict')),ensure_ascii=False))"
        )
        ready_persist_argv = [executable, '-BE', '-c', stdin_persistence, str(controller),
                              json.dumps(prepared['reference'], ensure_ascii=False)]
    command = shlex.join(submit_argv)
    return dict(status='SCOPED_ORDINARY_NOTICE_PLAN', work_id=work_id, marker=marker,
                target_surface_uuid=owner, work_order=work_pin, report=report_pin,
                body=body, wire_text=prepared['text'], body_reference=prepared['reference'],
                persist_body_argv_template=persist_argv,
                persist_body_argv=ready_persist_argv,
                persist_body_stdin=body if ready_persist_argv is not None else None,
                persist_body_stdin_encoding='utf-8',
                body_persisted=False,
                persist_body_file_argument_index=6 if persist_argv is not None else None,
                persist_body_template_requires_substitution=persist_argv is not None,
                persist_body_file_contract='Replace argv[6] with a new absolute file containing exact UTF-8 body bytes',
                persist_body_required=prepared['reference'] is not None,
                declared_completion_channel='ORDINARY_NOTICE',
                submit_capture_contract=dict(
                    capture_stdout=True, capture_stderr=True,
                    preserve_returncode=True, preserve_complete_streams=True,
                    receipt_stream='stdout on success; stderr may contain the complete receipt on nonzero exit',
                    rc75='NATIVE_PENDING: preserve the original attempt and reconcile only; never resubmit',
                    original_reconcile_argv=submit_argv + ['--reconcile-only'],
                    execution='Execute submit_argv directly with capture_output=True; do not pipe stdout alone'),
                submit_command_template=command, submit_argv=submit_argv, input_operations=0,
                acceptance=False, native_receipt=False, formal_callback=False,
                replays_original_input=False, controller_source=str(source), controller_skill=controller_skill,
                controller_validation='REQUIRED_AT_PERSISTENCE_AND_SUBMISSION')


def prepare_body(result):
    """Opt-in exact body persistence; no input, callback or work acceptance."""
    if not result['persist_body_required']:
        result['body_persisted'] = True
        return result
    skill = result['controller_skill']
    _pin(skill['path'], skill['sha256'], MAX_WORK_ORDER_BYTES)
    completed = subprocess.run(result['persist_body_argv'],
                               input=result['persist_body_stdin'].encode('utf-8'),
                               capture_output=True, timeout=20)
    if completed.returncode:
        raise ValueError('original-controller body persistence failed; no input submitted')
    reference = result['body_reference']
    raw, proof = _pin(reference['path'], reference['sha256'], MAX_WORK_ORDER_BYTES)
    if raw != result['body'].encode('utf-8') or len(raw) != reference['bytes']:
        raise ValueError('persisted notice body differs from exact plan; no input submitted')
    result.update(body_persisted=True, persisted_body=proof, persistence_input_operations=0)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('work-order', 'work-order-sha256', 'report', 'report-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--prepare-body', action='store_true',
                        help='persist the exact long body through its issued controller; send no input')
    args = parser.parse_args(argv)
    try:
        result = plan(args.work_order, args.work_order_sha256, args.report, args.report_sha256)
        if args.prepare_body:
            result = prepare_body(result)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='SCOPED_NOTICE_PLAN_REFUSED', error_type=type(exc).__name__,
                              error=str(exc), input_operations=0), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
