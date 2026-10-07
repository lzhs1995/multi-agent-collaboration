# Read-only submission confirmation and native receipts

Both supervisor prompts and executor callbacks require evidence after Enter.
An editor echo, queued input, tool exit zero, or unrelated activity is not proof
of consumption. Inspect the original attempt before recovery; do not repaste an
uncertain delivery or modify a callback's frozen report.

`scripts/cmux_submit_confirmation_guard.py` is a read-only PostToolUse checker.
It resolves each actual call separately through `cmux_submission_inputs.py`;
quoted examples and tool output do not supply targets. Dynamic unresolved input
remains unverified. Current compose/queue evidence overrides historical success.
Confirmed task/callback evidence must match the full payload, original identity,
attempt, and unchanged file pins. The checker never sends keys or writes receipts.
It is a client hook, not an operating-system sandbox. `manage_install.py` registers
it on PostToolUse for both clients; doctor and harness wiring checks require it.
Installing a configuration does not hot-reload existing clients. Tests exercise
installation, removal and missing registration in temporary configuration roots.

`scripts/delivery_receipts.py` can reconcile an exact callback already present as
a native Codex user message. It checks the original task, receiver session,
workspace binding, report, finalization time, and available durable attempt. It
locks that attempt and atomically publishes a receipt without consuming a send
retry. Tool echoes, assistant quotes, changed reports, and boolean schema versions
are rejected. Native receipt proves reception, not acceptance of report claims.

Compatibility is intentionally limited: an existing `*-attempts` journal or
`.pending.json` requires the original reconciliation controller. It must not be
silently interpreted as an absent attempt. An old bound bridge remains subject
to its original task skill; this module does not migrate sender state. The legacy
sender and its bounded late-ACK recovery are unchanged.

### Existing journal: exact native reception

`callback_native_evidence.validate` checks an existing journal against the exact
native receiver user record. It creates evidence only. Explicit receiver-side
`reconcile_received` may publish a receipt using the original bound bridge and
existing delivery lock: authenticate the receiver session and live participants,
check original pack/report/attempt hashes and post-send native text, revalidate
the evidence and lock inode, then atomically publish without overwriting. It never
pastes, presses Enter, disarms a task, or changes availability. A legacy pending
file, active sender lock, replaced inode or existing receipt refuses settlement.

The journal reader revalidates native evidence before accepting this receipt.
Do not install that reader alone over an incompatible bridge. Preserve fixed
task controllers; test their receipt contract separately. A real receiver-side
settlement proves receipt, not a subsequent executor Stop invocation, global
deployment, report acceptance, or success of the underlying research. Record
those results independently. This is explicit recovery for already-delivered
messages, not permission to fabricate a missing attempt or replay a callback.

Validation: `python3 -B -m unittest discover -s scripts -p 'test_*.py' -q`.
The semantic command checker runs in a subprocess to prevent a script-level
`sys.exit` from prematurely terminating test discovery. All examples are synthetic.
Offline test results, hook registration, loaded runtime, live delivery and final
report acceptance must be recorded separately.


## Full visible draft ownership

The sender now uses the exact-composer renderer when deciding whether an additional Enter or the displayed Codex Tab action belongs to its original payload. It no longer deletes all whitespace before comparison. Full content, indentation and unknown footer rows are preserved; known wrapping, empty model-footer gaps and the measured single Claude cursor cell are display equivalences, not access to native editor bytes. Whitespace-only content rows are not empty footer gaps. Folded paste summaries and extra content cannot authorize another key. This selectively changes draft ownership; the original callback journal and late-ACK controller remain authoritative. It does not certify a new native delivery or migrate the complete installed sender.

## Ordinary handshake and status messages

The public submit-text entry now requires a visible stable marker and persists
payload, caller, workspace, receiver and pane identity in message-dispatch-v1.
Paste and Enter intentions are written before terminal input. A queued or
uncertain attempt must use the same marker with --reconcile-only; it must never
be repasted. Only a recorded zero-input failure permits one explicit retry.
Changed payload or identity, existing receipts, and legacy attempts fail closed.
Forced compose replacement is refused for ordinary messages.

The post-submit hook independently reads the original receipt and observations,
checks their hashes and complete received payload, and performs no terminal input.
Task dispatch, callbacks and ordinary messages share the existing deliveries-v1
receiver lock; task journals additionally retain their historical target lock.
This prevents different message classes from simultaneously typing into one
receiver. Low-level observer calls are reserved for those original controllers.

These are source behavior changes. Passing offline tests does not establish
that a pinned installed release or running client has loaded them. Preserve old
controllers and journals until a separately verified runtime transition.

## Keep the reader and bridge in one version root

A reader-only overlay is not a supported transition. A focused comparison found
that the retained bridge lacked `_delivery_confirmed`, which the new journal
reader requires: the mixed pair failed five of nine cases (two failures and
three errors), while the complete candidate passed all nine. Broad suites with
unchanged failure names did not detect this incompatibility reliably.

Point new-task wrappers and hook commands at the same complete, pinned version
root. Before applying a scoped transition, preserve original bytes, modes and
hashes, validate exact changes, and use the supported mutation lock and
compare-and-swap installer. Do not overwrite foreign skill directories or
rewrite historical task controllers and journals. Verify actual import roots,
wrapper startup and semantic hook cases after installation. Configuration edits
still do not prove existing clients reloaded or that callbacks were delivered.

For a late handshake ACK, retain the expired receipt, original detector error and
task/provider/nonce. Recheck live workspace and peer identity, parse the actual
assistant response with the canonical ACK parser, and publish the recovery
through the original receipt writer. Do not resend the challenge, invent an
earlier submission time, or blame executor silence for a supervisor timeout
below the phase minimum. Finalize a task pack before dispatch and include every
required prompt binding; a pre-input contract refusal is not a sent task.
