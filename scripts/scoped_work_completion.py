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


def plan(work_order, work_order_sha256, report, report_sha256):
    """Read both exact UTF-8 bodies once and describe the declared ordinary route."""
    work_raw, work_pin = _pin(work_order, work_order_sha256, MAX_WORK_ORDER_BYTES)
    report_raw, report_pin = _pin(report, report_sha256, MAX_REPORT_BYTES)
    order = json.loads(work_raw.decode('utf-8'))
    report_raw.decode('utf-8', errors='strict')
    if not isinstance(order, dict) or not isinstance(order.get('schema'), str):
        raise ValueError('issued scoped work order required')
    if (order.get('completion_callback') or order.get('formal_harness_receipt') is True
            or order.get('task_id') or 'scoped' not in order['schema']):
        raise ValueError('formal harness task requires its original callback route')
    outputs = order.get('outputs')
    if not isinstance(outputs, dict):
        raise ValueError('issued output paths must be an object')
    declared = outputs.get('final_output') or outputs.get('report')
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
    progress, acceptance = outputs.get('progress_path'), outputs.get('acceptance')
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
    canonical = Path.home() / '.agents/skills/multi-agent-collaboration/SKILL.md'
    controller = canonical.resolve(strict=True).parent / 'scripts'
    from cmux_prompt_reference import plan as reference_plan
    prepared = reference_plan(body, marker)
    # Use the actual public CLI with bounded wire text as one argv item. Shell
    # quoting is for display only; execute submit_argv without interpolation.
    executable = str(Path(sys.executable).resolve())
    submit_argv = [executable, '-BE', str(controller / 'cmux_bridge.py'),
                   'submit-text', '--surface', owner, '--text', prepared['text'],
                   '--marker', marker, '--wait-seconds', '0']
    persist_argv = None
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
    command = shlex.join(submit_argv)
    return dict(status='SCOPED_ORDINARY_NOTICE_PLAN', work_id=work_id, marker=marker,
                target_surface_uuid=owner, work_order=work_pin, report=report_pin,
                body=body, wire_text=prepared['text'], body_reference=prepared['reference'],
                persist_body_argv_template=persist_argv,
                persist_body_required=prepared['reference'] is not None,
                declared_completion_channel='ORDINARY_NOTICE',
                submit_command_template=command, submit_argv=submit_argv, input_operations=0,
                acceptance=False, native_receipt=False, formal_callback=False,
                replays_original_input=False, controller_source=str(controller.parent),
                controller_validation='REQUIRED_AT_PERSISTENCE_AND_SUBMISSION')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('work-order', 'work-order-sha256', 'report', 'report-sha256'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        result = plan(args.work_order, args.work_order_sha256, args.report, args.report_sha256)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps(dict(status='SCOPED_NOTICE_PLAN_REFUSED', error_type=type(exc).__name__,
                              error=str(exc), input_operations=0), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
