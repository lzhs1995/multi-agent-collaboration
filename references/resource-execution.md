# Bound resource execution for new tasks

Use this opt-in API when a newly authorized task must bind a supported local
resource operation to its actual executor. It extends the original
`resource_broker.py` database and FIFO queue; it does not migrate existing tickets.
The adapter at the real application entry must call it. A federation declaration
or lease file alone does not enforce execution.

## Contract and entry points

Build one `resource-execution-v1` contract per operation. It contains:

- The task, resource, operation, request revision SHA256 and original attempt ID.
- The actual executor's workspace, surface, pane and native session UUIDs.
- The absolute controller source and the complete `scripts/*.py` closure, with
  relative path, byte count and SHA256 for every member.
- Exact executable and arguments, canonical working directory and complete
  environment. Include the executable and transport files in `transport_artifacts`.
- Existing operation inputs and transport artifacts as absolute
  `{path, bytes, sha256}` references.

Call `Broker.validate_execution_contract(contract)` before opening a broker or
changing a budget. This static method performs read-only input, controller and
native caller checks. Input preparation may create new task-owned immutable
files before preflight; preparation does not submit an application request.

After preflight, use the original configured broker root:

```python
ticket = broker.request_enforced(contract)
lease = broker.grant_enforced(ticket['id'], ttl=600)
bound = lease['execution']
result = broker.run_enforced(
    ticket['id'], lease['token'],
    generation=bound['generation'],
    operation=bound['operation'],
    request_revision=bound['request_revision'],
    attempt_id=bound['attempt_id'],
)
```

The application adapter supplies its original resource-specific terminal proof
to `release_enforced`, together with `execution=result['execution']` and the
same generation. A ticket with no invocation instead requires
`no_request_submitted=true` and the original lease execution binding. Neither
proof substitutes for Word document/window checks or NLM remote completion.

The broker keeps metadata in side tables, leaving original ticket/resource row
formats intact. Generations increase in the same database transaction as grant.
Generic request/grant/run/release paths cannot downgrade an adopted operation.
The executing actor may be a named resource worker; it need not be the senior
supervisor. A role label never replaces native caller authentication.

If the original lease was released with verified zero invocations and no launch
intent, `requeue_unsubmitted_enforced(ticket, token, generation=...)` can put
that same operation at the queue tail. It requires the original no-submission
release proof and exact native actor. Before requeueing, the same database
transaction archives the complete old lease, binding, proof and generation.
The next grant gets a new generation; the old token cannot run. This exception
does not apply to UNKNOWN, any recorded launch intent, an invoked operation or
a legacy ticket. It preserves the request and application attempt rather than
resetting their history when a quota refusal occurred before submission.

An original actor may cancel a still-WAITING, generation-null ticket through
`withdraw_enforced(ticket, attempt_id=..., contract_sha256=...)`. Cancellation
authenticates the original controller/session and preserves the original row and
binding in the cancellation ledger. It does not require an invalidated input,
transport or working directory to become valid again. Any invocation, launch
intent, ACTIVE lease or UNKNOWN outcome requires its original reconciliation;
withdrawal never launches an application or removes the original evidence.

## Intent, uncertainty and scope

Before `Popen`, persist the original intent and invocation in the broker database.
Recheck the current native actor, input pins, operation and generation while
holding the original resource and external locks. A recorded intent is consumed
even if a later check or launch fails. Exceptions retain UNKNOWN and the original
active lease; they do not create another application attempt.

Native identity collection runs outside the SQLite write transaction while the
original resource locks are held. The short transaction rechecks the observed
rows, local pins and generation. This avoids blocking unrelated resources for
the duration of native collection; collection and use remain separate operations.

Only the original registered process group can support bounded cleanup and
readback. A crash between spawn and process registration remains unresolved.
UNKNOWN reconciliation requires exact original-attempt evidence and the original
resource's terminal conditions. A timeout, expired lease, empty local process
group or a self-reported zero-effects flag alone cannot establish remote outcome.

These checks cover the supported local launch path. They do not intercept direct
Word/osascript, Zotero API/MCP, browser or NotebookLM calls made through other
paths, nor revoke a request already accepted by a remote service. Process-local
locks cannot survive the guard's death. Inputs reopened by an external process
also retain a path-check/use race; use owned immutable snapshots and do not claim
OS-wide enforcement.

Inner Herdr receiver identity is not authenticated caller identity. If the native
caller checker cannot distinguish an inner executor, refuse before execution;
do not borrow the outer terminal's session or accept an environment selector.
Legacy controllers retain their existing identity rules. Offline tests that mock
native identity prove only the tested guard logic. Live client loading, actual
caller authentication, remote completion and research acceptance require separate
evidence.

## New Word PDF tasks: synchronous typed entry

For the explicit file-pinned command-line steps, see
[Word PDF execution entry](word-pdf-execution-entry.md).

`resource-execution-word-pdf-v1` is an additional opt-in contract. Existing
`resource-execution-v1` tasks retain their original API and meaning. The Word
contract has the same task/resource/actor/controller/operation/revision/attempt,
working directory, complete environment and input/transport references, but
replaces `argv` with `word_plan: {path, bytes, sha256}`. Its resource is exactly
`word-zotero` and operation is `owned_word_pdf_export`. There is no top-level
command that the implementation pretends to have executed: the fixed driver
calls the installed Python export service in the original process and records
each actual subprocess argv separately.

Prepare a task-owned immutable JSON plan with exactly these fields:

```json
{
  "schema": "word-pdf-stage-plan-v1",
  "input": {"path": "/absolute/owned/input.docx", "bytes": 1, "sha256": "64 lowercase hex"},
  "output": "/absolute/new/output.pdf",
  "output_directory": {"path": "/absolute/new", "dev": 1, "ino": 1},
  "services": {"word_runtime.py": {"path": "exact installed path", "bytes": 1, "sha256": "fixed source SHA"}, "word_pdf_service.py": {"path": "exact installed path", "bytes": 1, "sha256": "fixed source SHA"}},
  "executables": {"osascript": {"path": "/usr/bin/osascript", "bytes": 1, "sha256": "actual SHA"}, "pdfinfo": {"path": "/canonical/executable/pdfinfo", "bytes": 1, "sha256": "actual SHA"}, "pdftotext": {"path": "/canonical/executable/pdftotext", "bytes": 1, "sha256": "actual SHA"}},
  "locks": [{"path": "/Users/lzhs/.cache/wordrev/word-automation.lock", "dev": 1, "ino": 1}, {"path": "/Users/lzhs/.cmuxterm/locks/desktop-focus.lock", "dev": 1, "ino": 1}],
  "budget_seconds": 600,
  "required_free_bytes": 42949672960
}
```

This S40 typed slice retains the user's 40 GiB minimum; a plan may raise it but
cannot lower it. Historical controllers keep their separately registered limits.
The example values are a schema illustration, not usable pins. Read actual
bytes/SHA and device/inode values during task preparation. Both service files
must be the fixed `0.1.0.dev5-c68cc8aec9aa` package under
`/Users/lzhs/.local/share/officecli-word-revision/releases/` at its existing
`venv/lib/python3.14/site-packages/officecli_word_revision/scripts/` directory.
`word_execution.py` fixes their SHA values; a different service/factory is not
an allowed contract override. The complete MAC source closure also includes the
Word driver. Include the plan and DOCX refs in `inputs`, and every service and
executable ref in `transport_artifacts`. The two original lock files and the
output parent must already exist. The driver never configures a resource,
changes its release conditions, clears pending, or selects a new controller.

Use `Broker.validate_word_pdf_contract(contract)` before constructing a broker.
It performs fresh native actor authentication and makes no database connection.
Against an already configured original root, request and grant normally, then:

```python
lease = broker.grant_enforced(broker.request_enforced(contract)['id'])
bound = lease['execution']
result = broker.run_word_pdf_enforced(
    lease['id'], lease['token'],
    generation=bound['generation'], operation=bound['operation'],
    request_revision=bound['request_revision'], attempt_id=bound['attempt_id'],
)
```

The fixed stages are PROBE, PREFLIGHT, OPEN, RESOLVE_PATH, EXPORT, pdfinfo,
pdftotext and CLOSE. A single aggregate UNKNOWN intent and invocation are
committed before service construction; each stage has its own committed intent,
actual argv/source pin, PGID/process observation and result. Stage calls remain
in the original parent process and synchronous thread. The same exact loaded
`word_runtime` module actually enters its original `word_lock`, so the service's
nested acquisition uses its original `_HELD` reentrancy. The resource flock and
desktop-focus lock remain held across the entire service flow. No worker token,
manual `_HELD` value, `skip_lock` option or arbitrary runner is accepted.

Each runner entry checks pending independently because the original reentrant
lock does not repeat that check. Result-unknown errors after an intent, including
OPEN before the service sets its `opened` flag, are treated as uncertain. Existing
pending bytes are preserved. CLOSE failure is incomplete even when the service
has already published a PDF and reports `success=true`; cleanup, unchanged staged
DOCX/input, all eight terminal stages and every process group must also pass.
The fixed service performs local staging/copy/link/cleanup/receipt writes outside
the subprocess runner; the driver encloses the whole service, pins its source and
paths, checks before returning from a stage and verifies final artifacts. These
checks are not interception of arbitrary Python writes or other application
entry points.

Successful export returns `OWNED_PDF_COMPLETE_RESOURCE_STILL_ACTIVE`. It does not
release the lease. Only `release_word_pdf_enforced` accepts this typed mode;
ordinary enforced run/release reject it. Release requires the original execution
binding, aggregate digest, all stage PGIDs drained, actual pending absence and a
fresh original global observation. Its pinned `observed_receipt` must contain
the unchanged original Word release fields plus `execution` (all five bound
fields), `result_sha256`, `stage_digest` and numeric `observed_at`, collected after
the aggregate completed and no more than 30 seconds old. The original global
documents/windows/modal/Zotero conditions still run. A successful owned PDF with
foreign documents open cannot relax those conditions or close foreign work.
The typed entry checks the configured minimum requirements before any stage
and again during release: integer `documents=0`, integer `windows=0`, and boolean
`modal=false`, `zotero_current_doc=false`, `zotero_current_window=false`,
`pending=false`. Missing fields, relaxed values or different JSON types refuse;
additional requirements are allowed and still enforced by the original broker.
Ordinary APIs and existing resource configurations are not rewritten.

UNKNOWN release/restart/resume is deliberately unsupported in this first typed
version. `broker.inspect_word_pdf_enforced(ticket)` queries original ticket,
binding, attempt and all stage evidence without native calls, spawning or state
updates. It operates on an **already open Broker**; constructing `Broker(root)`
itself initializes SQLite and must not be advertised as a read-only inspection.
The readback does not authenticate the current caller, prove live process drain,
clear pending, or authorize replay. A spawn-before-PID-record window stays
UNKNOWN. Local bounded drain does not prove a queued Word AppleEvent finished.
Runtime adoption still requires a named real task and successful actual native
identity; offline fixtures do not supply either.
