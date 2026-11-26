# Read existing restricted resource records

`scripts/federated_resource_preflight.py` reads an existing restricted Word
ticket and its original plan, disposition, executor and controller bytes. It
never requests/grants a lease, executes an application, releases a ticket,
authenticates a caller or evaluates the original approval. Use Python `-B`.

For a known original ticket, use `--ticket ORIGINAL_TICKET_UUID`. This requires
no new sidecar and reports its original executor, task (where the original job
has one), controller reference and next readback action. It does not generate a
run command or expose the original token. A previous invocation, PENDING,
unknown/terminal result or expired lease always routes to original-attempt
readback, never another run.

The CLI fixes the local resource root at
`~/.local/state/multi-agent-collaboration/resources`. This is a scope restriction,
not authentication. It ignores resource-root environment overrides and has no
`--root` option. The Python API's explicit `expected_root` supports private test
fixtures; changing it never creates identity or authorization.

The source database is never opened by SQLite: even SQLite `mode=ro` may touch
the source WAL shared-memory file. The reader fixes bounded main DB and WAL
bytes plus device/inode/size/mtime/ctime/hash over consecutive reads, copies them
into its own temporary directory, and opens that snapshot with `mode=ro` and
`query_only=ON`. It reads committed WAL state, rechecks the source images and
removes only its own temporary directory. A changing source yields UNKNOWN;
there is no retry, checkpoint, application lock or mutation of the original
queue. A nonempty rollback journal is not interpreted by this helper.

Supported original dispositions are `word-restricted-disposition-v1` for
`OWNED_PDF_ONLY`, and `word-restricted-disposition-v2` or
`word-restricted-native-successor-disposition-v1` for
`OWNED_REVISION_ROUNDTRIP`. The reader checks exact stored references, original
resource mapping, full original `scripts/*.py` closure and input/job bytes. It
does not import the original controller. Native approval semantics, current
caller, service pins, application state, disk/locks and all execution-time guards
remain the original controller's responsibility.

An optional `--request /absolute/request.json` checks a business task against an
already existing job; it does not register a new one. Its exact fields are:

- `schema: federated-resource-preflight-v1`, canonical `resource_root`,
  `resource: word-zotero`, original `operation`, original `task_id` and `executor`.
- `executor` contains original workspace/surface/session UUIDs and pane UUID
  where present in the original plan. Role labels are not accepted as identity.
- `existing` contains `ticket_id`, `ticket_sha256`, `plan_ref`, `disposition_ref`
  and `controller_ref`; these come from inspecting the original record.
  File references contain absolute canonical path, byte count and SHA-256.

Missing bindings produce BINDING_REQUIRED. An invented task ID cannot match an
original initial job. A PDF-only plan without an authoritative business task ID
may yield ORIGINAL_RECORDS_MATCH on inspection, but cannot bind a new task.
Do not change an old successor, job or disposition to satisfy this check.

| Result | Meaning |
| --- | --- |
| ORIGINAL_RECORDS_MATCH | Inspected original bytes agree; no new task binding or execution permission |
| ORIGINAL_CONTROLLER_RECHECK_REQUIRED | Existing job/task/actor references agree; original actor must still pass every original guard |
| ORIGINAL_RECONCILIATION_REQUIRED | Read the original attempt/outcome; no replay or automatic release |
| BINDING_REQUIRED | A declaration does not supply a matching original executable contract |
| UNSUPPORTED_ENTRYPOINT | This reader does not handle the requested operation/path |
| UNKNOWN / REJECTED | Source changed or a required binding failed; no mutation |

Exit 0 only means ORIGINAL_CONTROLLER_RECHECK_REQUIRED; all other results exit
2. Every result has `execution_authorized=false`, `native_executor_verified=false`,
`original_approval_verified=false`, `lease_granted=false` and `accepted=false`.
No result is a durable permit: original state may change immediately afterward.

This narrow reader does not inspect the newer opt-in `resource_execution.py`
side tables or the NLM execution facade. Those supported execution paths retain
their own pinned contracts and actual entry guards; use their original owner
documentation. Nor does this reader wrap arbitrary argv, direct MCP/API calls or
Zotero Refresh. UNSUPPORTED_ENTRYPOINT describes this reader's scope, not the
absence of another supported controller. Existing NLM requests continue through
their original bound runtime/reconciliation and shared account budget.

The synthetic tests demonstrate byte binding and read-only behavior, not native
approval, application use or live executor adoption.
