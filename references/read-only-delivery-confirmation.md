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
