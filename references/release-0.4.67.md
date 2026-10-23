# Original reply preflight and complete result receipts

`scripts/executor_reply_preflight.py` reads an exact state snapshot and a
prospective body by absolute path and SHA256. It checks the original WAITING
episode, bound peers/task/episode, actual marker issuance (including signed late
markers), the continuing consumed frontier, supported status and wait owner/
deadline. It rereads bytes and file identity before returning. Strict UTF-8,
duplicate keys, nonstandard constants and recursive fallback parser failures
return a bounded refusal.

This is structural validation, with `authority_verified=false`. It never
publishes, sends, consumes, enrolls, releases or authenticates a supervisor.
The original authenticated supervisor still checks live authority before
publication; the executor FULL-reads and consumes through its original controller.
Preserve a refused body and prepare a new candidate instead of rewriting history.

```bash
python3 -BE scripts/executor_reply_preflight.py \
  --state-file /absolute/original-state-snapshot.json --state-sha256 EXACT_STATE_SHA256 \
  --body-file /absolute/prospective-reply.json --body-sha256 EXACT_BODY_SHA256
```

`STANDBY`, `HOLD` and `WAIT` are not mailbox statuses. Use the existing
`WAITING_DEPENDENCY` status with a concrete trigger; continuing waits also retain
the original supervisor owner and finite ordered due/deadline values.

New scoped completion plans specify complete stdout/stderr capture and retain
the return code. A pending submission can print its entire receipt on stderr
and exit75. Keep both streams and the original attempt; use the generated
`original_reconcile_argv`, retaining original `--surface`, `--marker` and `--text`.
Do not repaste or send another Enter after an uncertain result.

Freeze independent results before reporting final counts. A separate actual
FULL-read boundary precedes output generation; hashing a file or printing it
inside the same generation command does not establish that boundary. Review
limits and late increments remain explicit.

The complete original 0.4.66 file producer and its historical issuer range are
pinned for compatibility. No original attempt or controller is migrated. Source
validation, release, installation, automatic client loading, useful delivery and
overall task efficiency require separate evidence.
