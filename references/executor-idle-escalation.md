# Executor idle escalation (2026-10-08)

## Incident

Task `cmaverse-live-install-proof-20261008-r20` armed executor surface:2596 at
04:16Z. No handshake or finalized task pack followed for two hours. The
executor's one task request (04:21Z) sat behind the supervisor's 5h+ busy turn.
The protocol forbade resending it, and the Stop guard allowed every idle turn
("no finalized executor task pack is active"). Result: an executor that waited
with no bounded next step, and a user who had to ask "is it interrupted?" for
two hours.

A push attempt at 06:19Z showed a second fact: the supervisor's viewport was
scrolled up (`New activity · Earlier messages available`), so the bridge
refused input before paste (`receiver_kind=UNKNOWN`, journal `NO_INPUT`). Push
can be unavailable for a long time; a pull-side notice is therefore part of
every escalation.

## Rule

While an armed executor has no finalized task pack, its idle clock runs from
the latest supervisor-side activity: marker `armed_at` / `last_activity_at`,
any `handshake-receipt.json` executor `created_at`, the mtime of any top-level
file in the artifact root (before dispatch only the supervisor writes there:
preflight evidence, receipts, draft pack), and a supervisor `ack` (below).
Bare (timezone-less) timestamps are ignored. Any of these is a "reply".

Ask again every 60 s until the supervisor replies (user directive,
2026-10-08: "设定一个时间阈值，比如一分钟60秒，没有回复就再问一次，循环往复，
session不能以任何理由中止，直到 codex supervisor 回复为止"):

| escalation | due |
|------------|-----|
| #1 | 60 s idle |
| #2, #3, … | 60 s after the previous record, no cap |

An escalation is never due earlier than 60 s after the previous record, so a
late one cannot cascade into a burst. The marker ttl bounds the total.

### Two processes, one sender

- `watch` (background, detached) is the only sender. It heartbeats, re-reads
  the marker, escalates when due and sleeps at most 30 s. An ack only restarts
  its clock. It exits 0 on dispatch (finalized pack), 3 when the marker is gone
  or expired, and 8 at once if another live watcher already covers the task.
- `pursue` (foreground) is the executor's step. It (re)starts the watcher when
  none is alive (pid running, heartbeat ≤ 90 s), then waits. Exit 0 = the
  supervisor replied or dispatched; exit 9 = no reply within 540 s (under
  10-minute tool timeouts); exit 3 = marker gone. It never sends itself.

### The session does not end while unanswered

Past 60 s with no reply, the Stop guard blocks turn-end, **including on
Stop-hook reentry** (`stop_hook_active=true`), and names the command:

```bash
python3 -B scripts/executor_idle_escalation.py pursue --task-id <id> --executor-uuid <uuid>
```

A live background watcher does not lift the block; only a reply does. A reply
restarts the idle clock, so the executor can end its turn at once and leave its
input free for the supervisor's handshake. If a supervisor preflight against
the busy executor fails (`COMPOSE_OCCUPIED`), its evidence file is itself a
reply: the executor's `pursue` returns within seconds, and the supervisor
retries. Reentry checks run identity discovery only when some fresh marker
actually has an unanswered executor; otherwise reentry still terminates
without discovery. A user interrupt always ends the loop, and marker expiry or
disarm ends the rule.

Each escalation (`escalate`, also callable by hand):

1. Creates `tier-N.json` with `O_EXCL` before any terminal input (the dispatch
   lock; a concurrent or repeated call cannot send twice).
2. Writes `notice-tier-N.json` with the full text (pull channel).
3. Sends a NEW ordinary message with marker
   `EXECUTOR_IDLE_ESCALATION_T<N>_<uuid8>_<UTC>` through
   `cmux_bridge.submit_text` (journaled, compose-preserving, never forced).
4. Records the outcome from the message journal: `CONFIRMED`,
   `SUBMITTED_UNCONFIRMED`, `NO_INPUT` or `TRANSPORT_ERROR`.

Any outcome counts as the executor having acted. A record never claims the
supervisor read the message. State lives under
`~/.local/state/multi-agent-collaboration/executor-idle-escalation-v1/`, keyed
by task, collaboration, executor and idle clock start; new supervisor activity
opens a fresh ladder.

A confirmed delivery is not a reply; the repeats continue until
supervisor-side activity appears. While the supervisor's input is still
occupied by an earlier unconfirmed message, later pushes may record
`NO_INPUT`; the notice files carry them regardless.

## Supervisor reply without dispatch

When the supervisor has seen the request but cannot dispatch yet, it stops the
repeats from its own surface (the caller must be the marker's supervisor; an
executor cannot ack itself):

```bash
python3 -B scripts/executor_idle_escalation.py ack --task-id <id> --executor-uuid <executor-uuid> [--hold-seconds 1800]
```

The ack restarts the idle clock at `acked_at + hold` (hold capped at 2 h), so
asking resumes 60 s after the hold if nothing else happens.

## Other commands

`status` prints the idle clock, records, next due time and watcher liveness.
`wait` is the bounded in-turn poll (exit 0 = dispatch arrived, 6 = escalation
due, 7 = window elapsed, 3 = no unique armed marker).

## Boundaries

- Not a resend. Earlier messages and callbacks keep their original journals and
  may only be reconciled read-only.
- Not a task dispatch. The text never starts with `TASK:`; the bridge rejects
  that shape for ordinary messages.
- Finalized packs are governed only by the completion-callback rules.
- The supervisor and non-participants are never subject to this rule.
- Stop reentry (`stop_hook_active=true`) still terminates for everyone except
  a positively identified unanswered idle executor; identity discovery failure
  on reentry terminates as before.
- Cost: each blocked Stop is one more model turn. `pursue` waits up to 540 s
  per call, so a long-unanswered executor spends about 7 turns per hour.
