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
It is a client hook, not an operating-system sandbox. This change supplies the
entrypoint but does not add installer registration or hot-reload existing clients.

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
