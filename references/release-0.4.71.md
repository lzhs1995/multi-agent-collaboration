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

The protected ordinary-message driver is byte-identical to the reviewed
0.4.70 driver. Explicit zero-input recovery therefore also recognizes the
complete 0.4.71 controller with that exact driver hash. Unreviewed versions,
including 0.4.72, still refuse before input. History, original-controller and
single-use recovery checks remain unchanged.

An issued order may also name its final output as `outputs.final_path`. All
supplied output aliases must still agree, and acceptance, progress and final
paths remain separate. The planner never rewrites the issued order or report.

The supervisor inbox's historical-reply fallback now uses the existing
open-marker and continuing-wait validator. A consumed historical reply cannot
hide a newer unanswered continuing inquiry. An authenticated current or open
late reply still ends that wait; noncontinuing inquiries retain their behavior.
Discovery remains file-only and never consumes a reply or changes its original
controller, queued input, reservation or unknown delivery state. Real automatic
exposure and original-executor reading/consumption require post-install evidence.
