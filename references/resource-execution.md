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
