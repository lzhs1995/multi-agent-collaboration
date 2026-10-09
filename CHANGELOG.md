## 0.4.0 — 2026-10-09

- Preserve literal escapes, tabs and newlines with guarded terminal.paste and
  submit_key=none; never fall back to the escape-decoding CLI send path.
- Confirm delivery only from a new, exact native user record after the original
  PASTE_INTENT EOF fence in the bound receiver process, session and transcript.
  Queued input, key success and a cleared composer remain unconfirmed.
- Use one guarded submission route with a stable complete draft, a shared bounded
  recovery budget and read-only reconciliation. Formal task/callback recovery
  remains read-only; the verified helper delegates ordinary messages to this route.
- Allow honest WAITING_SUPERVISOR closeout, task-bound diagnostics and authenticated
  supervisor report discovery. Idle requests and continuation waits are finite.
- Migrate only owned client hooks and preserve foreign configuration. Retire the
  obsolete screen-only and send-proof Stop guards; install the same full release
  for sender, reader, helper and hooks.

Migration: resolve the full installed release for new tasks. Preserve the original
controller, journal and evidence of in-flight tasks; never backfill a missing
pre-input binding or fence. Installation, live client loading, Claude-to-supervisor
reception and business acceptance require separate evidence.

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
