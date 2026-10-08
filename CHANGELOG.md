## Unreleased — executor idle pull (2026-10-08)

Executors no longer wait silently after a callback. Before the honest handoff the Stop guard requires one file-only idle request (`scripts/cmux_idle_pull.py --task-pack`), which the closeout guard admits as the single exact command. The supervisor's Stop is blocked while an addressed request is neither followed by a newer CONFIRMED task dispatch nor acknowledged with a reason; unconfirmed (queued or no-input) attempts leave it pending, and an ack is written only when the live CLI caller resolves to the addressed supervisor surface in the same workspace. The request itself sends no terminal input, so a busy supervisor cannot lose it. The same command starts a detached `scripts/cmux_idle_push.py`. It re-asks the supervisor every 60 s with new marked STATUS messages, for up to 24 h, until the supervisor answers with an ack, or a newer confirmed dispatch or message. The executor's handoff requires that pusher to be live. See `references/executor-idle-pull.md`.

## 0.1.1 — 2026-09-26

Check current agent input before sending, preserve user drafts and queued work, and distinguish unmarked submission from confirmed consumption. Preserve original sessions and late-delivery evidence.

# Changelog

## 0.1.0 - 2026-09-08

- Initial public extraction of the macOS/cmux collaboration harness and tests.
- Portable canonical skill path, current-model preservation and opt-in compose override.
- Session continuity, strict ACKs, task/report-bound callbacks, evidence and lease guards.
- Callback-first sentinel, bounded compact/error recovery and explicit solo handoff.
- Non-destructive dual-client installer, doctor and uninstall with regression tests.
- Shell payload recognition excludes file-edit content and covers both command/cmd fields.
- Active-marker schema remains version 1. No claim of new live agent consensus.

Versioning: SemVer for this distribution; breaking public schema changes require
a major version after 1.0. Before 1.0, breaking changes require a minor bump and
explicit migration notes. Published tags and release assets are never replaced.
