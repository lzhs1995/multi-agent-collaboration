## Unreleased — 2026-10-08

Add the executor idle Stop guard and `executor_ready.py`: an idle executor keeps asking its supervisor every 60 s from a loop that outlives its turns, instead of waiting silently or asking once. Release requires a live loop, a reply that actually reached the executor (mailbox file or own transcript), or an operator stop; no reminder budget and no reentry escape. Asks are never stacked while the supervisor's input still holds the previous one. Fail-open identity. Not installed into running clients by this change.

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
