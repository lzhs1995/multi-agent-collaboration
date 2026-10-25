# Domain work orders and ordinary completion notices

The result planner previously rejected an explicitly ordinary work order when
its domain schema did not contain `scoped`. The executor had to construct a
separate ordinary notice after its result was already frozen.

Version 0.4.71 accepts that schema when the issued order explicitly declares
`completion_channel=ORDINARY_NOTICE`. Legacy scoped orders retain their default
route. Formal task IDs, callbacks and completion or callback nonces remain
refused. The three separate output paths, original supervisor marker, exact
report hash and complete issued controller still apply.

Planning remains read-only. `--prepare-body` persists only the exact notice body;
submission and native receipt remain separate steps. An earlier submitted or
uncertain notice is reconciled through its original controller rather than sent
again. See [the completion contract](scoped-completion-and-reply-status.md).

The complete immutable0.4.70 file producer retains only the controllers and
capabilities it originally supported. This adds no registration, consumption,
reservation-release or resource authority to an existing episode.
