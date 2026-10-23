# Exact executor pair and successor diagnostics

Version 0.4.66 adds a read-only diagnostic for a supervisor's two designated
original Claude executors. It does not enumerate a substitute pair from titles,
mtime, configuration or a count of enrolled sessions. The authenticated native
foreground thread must match the explicitly supplied supervisor/session and
workspace. Each original executor must match its exact session, task and overall
task. An empty task ID is supported when it is the original recorded value.

Create an absolute UTF-8 JSON scope, freeze its SHA256, and run through the
complete installed controller:

```json
{
  "schema": "executor-pair-coverage-scope-v1",
  "supervisor_surface_uuid": "11111111-1111-4111-8111-111111111111",
  "supervisor_session_id": "22222222-2222-4222-8222-222222222222",
  "workspace_uuid": "33333333-3333-4333-8333-333333333333",
  "executors": [
    {"surface_uuid": "44444444-4444-4444-8444-444444444444", "session_id": "55555555-5555-4555-8555-555555555555", "task_id": "original-A-task", "overall_task": "original-overall-task"},
    {"surface_uuid": "66666666-6666-4666-8666-666666666666", "session_id": "77777777-7777-4777-8777-777777777777", "task_id": "", "overall_task": "original-overall-task"}
  ]
}
```

```sh
python3.14 -BE /absolute/installed/source/scripts/executor_reask.py coverage \
  --caller-uuid ORIGINAL_SUPERVISOR_UUID \
  --scope-file /absolute/exact-pair-scope.json --scope-sha256 EXACT_SHA256
```

UUIDs for surfaces/workspaces use the canonical uppercase form. Native session
IDs use canonical lowercase UUIDs. Duplicate peers, a changed scope SHA, a
different foreground thread and malformed JSON refuse before claiming coverage.
Both executor session IDs must be distinct from each other and from the
supervisor's session. Duplicate sessions refuse before native observation.
The command has a shared 30-second native observation budget and a ten-second
slice per peer. A failed observation remains incomplete; the other peer is still
considered within the remaining budget. Files and total inputs have size limits.

Each row reports a concrete next step for absent episode, absent enrollment,
stopped/finished/expired authority, task or native identity mismatch, uncommitted
registration, unsupported original controller, changed files and an unread
reply. Stable enrollment with recorded active authority is the acceptance scope.
Provider availability, FULL reading, consumption, automatic hook loading and
useful results remain unmeasured. A covered pair exits 0; incomplete or refused
inspection exits 2 with one JSON document. No terminal input or inquiry-state
write is performed. Coverage does not itself grant registration or consume
authority. Only the original executor can use an explicitly authorized start or
reviewed successor transition. Preserve the previous episode and pending input.

Successor preview output now labels raw state/enrollment/stop pins as
`LIVE_OBSERVATION` with `immutable_input=false`. They record a time-bound read;
they are not full-byte immutable work-order inputs. The reviewed plan still uses
the existing validated poll-only normalization. Asks, file posts, replies,
reservations, pending input and unknown semantic fields continue to change its
hash. This release does not widen the normalized polling fields.

For refusal details, add the exact original preview file and its full-byte SHA:

```sh
python3.14 -BE /absolute/installed/source/scripts/executor_reask.py register-successor \
  --caller-uuid ORIGINAL_EXECUTOR_UUID --supervisor ORIGINAL_SUCCESSOR_UUID \
  --authorization-file /absolute/authority.json --authorization-sha256 EXACT_AUTH_SHA \
  --reviewed-preview /absolute/original-preview.json --reviewed-preview-sha256 EXACT_PREVIEW_SHA \
  --apply --plan-sha256 ORIGINAL_REVIEWED_PLAN_SHA
```

The extra arguments supply diagnostic evidence only. A stale plan still refuses
before registration. Without the exact original preview, the refusal says that
field-level details are unavailable. Diagnostics list bounded component/field
names and hashes, never the pending payload values. Unsafe or over-limit field
names disable detailed field output while retaining the semantic hash. Read the
new exact preview and authorize its SHA through the original controller before
any subsequent transaction. No retry, old callback transfer, provider change or
task replay is authorized by this output. A post-commit exception retains an
unknown registration outcome rather than claiming zero state changes.

The complete installed 0.4.65 producer and its original issuer range are pinned
for file compatibility. New plan hashes include diagnostic semantic field hashes;
previous plans must not be applied through this newer controller. In-flight
plans and attempts retain their original complete controller. Installation does
not re-enroll any original episode, load every running client's hooks or prove
an improvement in end-to-end dissertation work. Measure actual request/body/
consume/result timing separately and retain timeout work that may have committed.

Scoped completion plans use the issued controller when CURRENT changes.
Supplied `controller`, `controller_source`, `completion_controller` and the
parent of `required_skill.path` must agree. Issued SKILL.md bytes and SHA256
are checked before planning. Orders without a controller retain the helper's
own source. No new plan selects a different release from the global skill link.
Existing uncertain attempts still retain their original controller and journal.

Output compatibility covers `outputs.progress` / `progress_path`,
`acceptance_output` / `progress_output`, and top-level `acceptance_path`,
`progress_path`, `final_report_path`. Supplied aliases must agree; three stage
paths stay separate, canonical and absolute. Original orders remain unchanged.

Add `--prepare-body` to persist the exact long notice body through the issued
controller and receive a ready `submit_argv`. This explicit operation writes
only the immutable body and sends no input. Default planning still writes
nothing. Programmatic callers can run `persist_body_argv` with exact UTF-8
`persist_body_stdin`, then submit; no temporary filename is needed. The older
file template remains supported with its substitution index explicitly set to
6. Notice receipt, FULL reading, acceptance and task release remain separate.
