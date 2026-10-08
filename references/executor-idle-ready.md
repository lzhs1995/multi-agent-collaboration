# Executor idle: keep asking until the supervisor replies

A disarmed executor is idle, not finished with the collaboration. The supervisor
may be busy for hours; an executor that ends its turn silently is invisible to
it, and one that asks exactly once is invisible again the moment that ask is
read and not answered. `cmux_executor_idle_guard.py` (Stop) therefore requires a
**live ask loop**, not a single request.

User directive, 2026-10-08: "60 秒没有回复就再问一次，循环往复 … 直到 codex
supervisor 回复为止" — re-ask every 60 s, no cap, and never end into a dead wait.
Finite-reminder and three-tier-ladder designs were rejected by this directive:
both stop asking while the task is still stalled.

## Why the loop lives outside the turn

The supervisor can dispatch only while the executor is NOT inside a turn: during
a running Claude executor turn, `compose_block_text` was `None` in 215 of 216
one-second samples (measured 2026-10-08), so the supervisor's bridge-test
pre-read records `COMPOSE_OCCUPIED` and its preflight exits 1 without sending.
So the executor must both keep asking and keep its turns short. An in-turn poll
satisfies neither: it blocks dispatch while it waits, and it stops asking at
exactly the moment the turn ends.

`scripts/executor_ready.py persist` is that loop: a detached process that asks,
sleeps, re-asks, and outlives every turn. The Stop guard does not ask the model
to be diligent; it refuses to release a turn while the obligation is open and no
such process is running.

## Contract

- Jurisdiction is positive: the guard obligates only a surface it has itself
  seen as an `executor*` participant of a fresh armed marker. Unbound sessions,
  other executors, non-Stop events and empty payloads pass without state.
- The idle episode starts when that binding disappears (disarm or TTL expiry).
- Release requires one of three real facts, never a budget:
  - `IDLE_ASKING` — a live loop covers this surface. Live means: state `ASKING`,
    heartbeat at most 180 s old, recorded interval at most 60 s, and the
    recorded pid still running the exact argv the loop recorded. A hand-written
    record, a stale heartbeat, a stretched interval, a foreign caller UUID or a
    stolen live pid do not pass.
  - `IDLE_ANSWERED` — a reply reached this executor during this episode.
  - `IDLE_STOPPED_BY_OPERATOR` — `executor_ready.py stop --caller-uuid <uuid>`.
- `stop_hook_active is True` does **not** release it. Reentry is not an escape
  hatch, because the remedy cannot fail by construction: `run_loop` writes its
  record before any bridge call, so even a refusing bridge leaves a live loop
  (the refusal is recorded as the ask outcome and retried next interval).
- A new armed binding ends the episode and signals the loop to stop
  (`reason=DISPATCHED`); that stop reason never releases a later idle episode.
- Identity failures fail open. This is a liveness guard, not a completion gate.

## What counts as a reply

Delivery is not a reply, and a decision written only in the supervisor's own
thread never reaches the executor. The loop stops on:

- **Mailbox**: any JSON file under the loop's mailbox directory, written at or
  after the loop started. The ask text carries the absolute path, so the
  supervisor can answer without touching a TUI at all. This is the channel that
  works while the executor's input is occupied or its viewport is scrolled.
- **Transcript**: a real received message in this session's own transcript —
  user turns and queued dispatches only. Tool results, meta entries, hook
  feedback, compaction summaries and the executor's own assistant text are its
  own context, never a reply.

`SOLO` and `WAITING_DEPENDENCY` are replies: they end the episode. Honour their
`resume_condition` instead of restarting the loop on a timer.

## Asking without stacking

Each attempt gets its own marker, record and journal, and is an ordinary
journaled message (never a task pack, never force-compose). Before a due ask the
loop re-reads the supervisor's own input: if the previous marker is still in its
compose block or pending queue, the attempt is deferred (`held_observations`) and
the clock restarts. So a busy supervisor accumulates one ask, not one per
minute. `UNCONFIRMED_DO_NOT_RESEND` means the original attempt stands; reconcile
with `reconcile --marker`, never paste a second copy.

## Commands

```bash
# started by the Stop guard's own printed command; also runnable by hand
nohup python3 -B scripts/executor_ready.py persist --supervisor surface:42 \
  --caller-uuid <executor-uuid> --task <id> --transcript <path> >/dev/null 2>&1 &

python3 -B scripts/executor_ready.py status --caller-uuid <executor-uuid>
python3 -B scripts/executor_ready.py stop   --caller-uuid <executor-uuid>
```

`status` prints state, liveness, ask count, heartbeat age, last ask outcome,
reply and last error. State lives under
`~/.local/state/multi-agent-collaboration/executor-idle-v1/`.

## Supervisor side

Treat every `EXECUTOR_READY` message as an action item at the next tool
boundary: dispatch a pack, or reply `WAITING_DEPENDENCY`/`SOLO` with the trigger
condition. Answer on the executor's surface or in its mailbox — a disposition
recorded only in your own thread leaves the executor asking, correctly. Dispatch
after the executor's turn has ended.

Installation does not hot-reload running clients; verify with `doctor`.
