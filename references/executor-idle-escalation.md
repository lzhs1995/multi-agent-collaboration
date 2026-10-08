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
any `handshake-receipt.json` executor `created_at`, and the draft
`task-pack.json` mtime. Bare (timezone-less) timestamps are ignored.

| tier | due at idle | min gap after previous record |
|------|-------------|-------------------------------|
| 1 | 10 min | 10 min |
| 2 | 30 min | 20 min |
| 3 | 60 min | 30 min |

Gaps are non-decreasing; a tier is never due earlier than its gap after the
previous record, so a late first escalation cannot cascade into a burst.

When a tier is due, the Stop guard blocks turn-end with the exact command:

```bash
python3 -B scripts/executor_idle_escalation.py escalate --task-id <id> --executor-uuid <uuid>
```

`escalate` sends exactly one escalation for the due tier:

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

Between tiers use bounded waiting, not open-ended polling of the supervisor:

```bash
python3 -B scripts/executor_idle_escalation.py wait --task-id <id> --executor-uuid <uuid> --max-seconds 1800
```

Exit 0 = dispatch arrived, 6 = escalation due, 5 = ladder exhausted,
7 = wait window elapsed, 3 = no unique armed marker.

After tier 3 the guard allows turn-end. The executor reports the block to the
user (task id, idle minutes, three outcomes, notice paths) and sends nothing
more for that idle clock.

## Boundaries

- Not a resend. Earlier messages and callbacks keep their original journals and
  may only be reconciled read-only.
- Not a task dispatch. The text never starts with `TASK:`; the bridge rejects
  that shape for ordinary messages.
- Finalized packs are governed only by the completion-callback rules.
- The supervisor and non-participants are never subject to this rule.
- Stop reentry (`stop_hook_active=true`) still terminates first.
