# 0.4.12 — 2026-10-10

- Distinguish literal document text from agent launches in a single quoted
  Python stdin heredoc. A HANDOFF paragraph beginning with an agent name no
  longer blocks a file update. This affects classification only.
- Retain the conservative launch scan for shell expansion, pipes, unknown
  interpreters or imports, executable references and aliases, invalid Python,
  and adjacent shell commands. Cover UTF-8 and CRLF document boundaries.

# 0.4.11 — 2026-10-10

- Authenticate either original endpoint when explicitly reconciling a callback
  without input. A supervisor can verify its received callback without being
  misclassified as a new sender targeting itself. Ordinary sends retain their
  existing identity checks.
- Parse callback modes strictly. Dynamic arguments, truthy strings, duplicates
  and conflicting queue modes cannot obtain receiver-side observation. Preserve
  original process, session, task, report and pre-input native-journal bindings.
- Keep reconciliation hints on the frozen task's original Python and controller.
  Missing or conflicting command metadata leaves an explicit unresolved hint,
  without discarding genuine native reception or substituting a newer release.

# 0.4.10 — 2026-10-10

- Treat exact bridge CLI help as a read-only operation, including literal
  Python script invocations and individual commands inside Codex tool batches.
  Help does not require a task pack, caller lookup or native delivery receipt.
- Continue checking real sends in mixed batches. Help text inside payloads,
  Python code and dynamically edited commands cannot exempt a send from native
  verification. Original controllers and in-flight attempts remain unchanged.

# 0.4.9 — 2026-10-10

- Recognize the optional goal-duration suffix on the standalone Claude clear
  hint only outside the complete composer borders. Draft text and unknown
  suffixes retain the existing zero-input refusal.
- Permit the non-submitting short bridge probe after an authenticated native
  Bash rejection/interruption chain with an empty composer and no surviving
  tool process. Recheck the bound transcript, process identities and owned draft
  before each cleanup key; any drift permanently invalidates the attempt.
- Keep formal submission, queue and native-reception rules unchanged. A residual
  Bash display is not a reason to restart a session or repeat a rejected tool.
  Preserve zero-input failures and continue the same task through its safe phase.

# 0.4.8 — 2026-10-10

- Add one optional launchd configuration guardian for immutable releases. Repair
  only owned Claude/Codex hooks and cc-switch Claude profiles, including imports;
  preserve provider selection, credentials, foreign hooks and unrelated settings.
- Bind executor replies to caller, supervisor, task, episode and an actually
  issued marker. Preserve late replies and unresolved terminal attempts after
  bounded history rollover; never replay an uncertain message.
- Share a nonblocking episode lock between explicit reasks and the optional Stop
  hook. Keep operator stops, authenticated task transitions and actual delivery
  counts separate from attempts and file writes.
- Add a bounded durable supervisor inbox to the existing PostToolUse hook, even
  without an active task marker. Resolve the native caller once per hook; file
  discovery is not native delivery or task acceptance.
- Document configuration restoration, current-client adoption, native receipt,
  callback settlement and return to the original task as separate evidence.

# 0.4.7 — 2026-10-09

- Emit verified PostToolUse results in the official
  `hookSpecificOutput` / `hookEventName: PostToolUse` / `additionalContext`
  envelope. Keep internal `action/results` out of the top-level client output.
- Validate success output against the Codex schema. Preserve silent unrelated
  calls and the existing failure behavior for unconfirmed delivery.
- Document configuration installation, automatic client invocation, exact native
  reception, callback settlement and task acceptance as separate checks.

# 0.4.6 — 2026-10-09

- Permit one synchronous original callback successor only when the sole prior
  attempt proves `NO_INPUT` with no events, receipt or pending legacy state, and
  the original task, report, attempt and controller pins still match.
- Keep the original controller's two-attempt limit and all live identity, draft
  and native-reception gates. Queued, uncertain or already received callbacks
  cannot use this exception; in-flight tasks are never migrated or replayed.
- Recognize the optional numeric rules-count field only outside matching Claude
  composer borders. Empty input and idle execution remain separate conditions.

# 0.4.5

- Accept exact-width Claude word wrapping and the observed clear-hint footer outside composer borders. Complete, stable, exact payload and native receiver evidence remain required.
- Preserve recovered original messages and settle genuine late ACKs against the original task/provider/nonce; the observation budget is not proof of executor failure.

# 0.4.4 — single-line fresh input and full native verification

- Preserve 0.4.3 nested native caller identity and current-status layout fixes.
- New ordinary/handshake messages containing CR/LF/tab or exceeding 700 UTF-8
  bytes use SHA-pinned single-line V2 references; old V1 attempts remain read-only.
- Formal packs use dedicated TASK_PACK_V2; callbacks retain exact dedicated text.
- Apply one shared single-line limit before every fresh paste.
- Update behavioral fixtures for task early rejection, literal heredocs, and
  historical multiline recovery without weakening live stable-draft/native gates.

## 0.4.3 — 2026-10-09

- Keep the hook's native caller collector through nested bridge reconciliation,
  retaining fresh process/tree checks instead of inheriting the daemon workspace.
- Recognize current Claude activity separately from quoted status in completed
  reports, and recognize Codex steer headers and the separate warnings footer.
- Add regression coverage for nested identity drift, real report layouts and
  active compaction/reconnection states without weakening native delivery proof.

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
