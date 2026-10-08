# Executor idle: ask for the next task, never wait silently

A disarmed executor is idle, not finished with the collaboration. The supervisor
may be busy for hours; an executor that ends its turn silently is invisible to
it. `cmux_executor_idle_guard.py` (Stop) enforces one bounded ready request.

## Contract

- Jurisdiction is positive: the guard obligates only a surface it has itself
  seen as an `executor*` participant of a fresh armed marker. Unbound sessions,
  other executors, non-Stop events and empty payloads pass without state.
- The idle episode starts when that binding disappears (disarm or TTL expiry).
  The executor runs `scripts/executor_ready.py request --supervisor <ref>
  --task <id>` once, then ends its turn. The message is an ordinary journaled
  `EXECUTOR_READY|<executor>|<marker>` line, never a task pack or force-compose.
- Evidence is the request record plus its own message-dispatch journal: same
  caller UUID, marker and payload hash, and a `PASTE_INTENT` after the binding
  was last seen. A final-message claim, a foreign journal, a hand-written
  record or a paste before the binding does not satisfy the guard.
- `UNCONFIRMED_DO_NOT_RESEND` means the original attempt stands (often queued
  behind the supervisor's tool call). Reconcile with `reconcile --marker`; never
  paste a second copy.
- Boolean `stop_hook_active is True` passes. At most two reminders per episode;
  then the guard records `IDLE_RELEASED_WITHOUT_REQUEST` and passes. Identity
  failures fail open. This is a liveness reminder, not a completion gate.
- A new armed binding starts a new episode. `WAITING_DEPENDENCY`/`SOLO` replies
  are supervisor decisions; the next task still arrives only as a handshake.

## Supervisor side

Treat every `EXECUTOR_READY` message as an action item at the next tool
boundary: dispatch a pack, or reply `WAITING_DEPENDENCY`/`SOLO` with the trigger.
Dispatch after the executor's turn has ended. Measured 2026-10-08: during a
running Claude executor turn, `compose_block_text` was `None` in 215 of 216
one-second samples, so the bridge-test pre-read records `COMPOSE_OCCUPIED` with
an empty-string preexisting hash and the preflight exits 1 without sending.
That reading is an unobservable compose block, not a user draft. A bounded
re-read before failing closed is a known open improvement, not implemented here.

Installation does not hot-reload running clients; verify with `doctor`.
