## 0.4.34

- Accept one exact native ACK text block alongside structurally valid Claude
  `thinking` or `redacted_thinking` blocks. Keep those contents opaque and out of
  result/archive output; reject additional text, tool blocks, malformed blocks,
  altered whitespace/nonces and non-boolean sidechain/meta flags. Preserve the
  original caller, session, fence, controller, receipt and dispatch checks.
- Preserve cross-release file scheduling for the fully verified 0.4.33 v3
  producer/v2 issuer. Its historical range includes the 0.4.32 controller;
  0.4.32 cannot claim a future 0.4.33 or CURRENT controller. Compatibility never
  consumes replies, moves registrations or executes historical release code.
- Targeted ACK validation: 40 behavior tests pass both locally and through the
  complete original 0.4.32 controller. The final offline suite passes all 1,912
  tests with no failures or errors. Installation and active-client adoption
  require separate evidence.

## 0.4.33

- Recognize the measured Claude HUD context token breakdown, such as
  `上下文 █████████░ 93% (输入: 78k, 缓存: 854k)`, only below matching
  complete composer borders. Counts follow the HUD renderer's integer,
  rounded `k`, and single-decimal `M` formats.
- Preserve matching text inside drafts verbatim. Unknown statistics,
  suffixes, units and incomplete borders still refuse input; recognizing
  the footer does not permit ordinary messages during compaction or reconnect.
- Add a synthetic observed-layout fixture and offline parser/delivery tests.
- Reconcile a genuine late ACK after the original handshake ended in
  `REJECTED / DRAFT_NOT_READY / STRANDED_IN_COMPOSE`. Require the exact
  original paste/observation history with no submission, the original
  supervisor and a matching native assistant ACK after the original fence.
  Preserve original evidence, submission state and controller; do not send input
  or promote a queued notice to native reception.
- Targeted ACK validation: all 35 behavior tests pass both locally and through
  the complete original 0.4.32 controller. Final combined offline validation:
  1,906 tests passed with zero failures or errors.
- Preserve file scheduling for the fully verified 0.4.32 v3 producer and v2
  issuer after CURRENT advances. Pin its complete manifest, controller and
  scheduler combination and reviewed descriptor. Historical re-enrollment
  support is bounded to the controllers its issuer actually supported; no
  controller migration, old code execution or reply consumption is authorized.

## 0.4.32

- Preserve the scheduler's rotation cursor before full-release verification.
  A timeout while reading the cursor leaves the original STATUS bytes intact
  and records LAST-ERROR separately. Report phase and per-episode durations,
  the interrupted enrollment and incomplete file-post counts without weakening
  the 10-second budget or source verification. Four targeted deadline and
  partial-commit cases pass; live latency still requires separate evidence.
- Distinguish each foreground consume call from the continuing episode state.
  Return an explicit consumed result with receipt/body/time evidence on success;
  unanswered, stopped, missing and foreign-controller attempts exit 2 without
  claiming consumption. Preserve formal pauses and old controller semantics.
- Treat Claude queue removal and queued-command attachments as pending client
  evidence with no promise of a later native user record. Avoid competing
  self-wakeups during a live dispatch; native Goal, loop and advisory evidence
  remain separate.
- Add explicit late ACK reconciliation through the complete original controller.
  Only the original supervisor can apply after the expired harness exits; retain
  timeout archives, dispatch uncertainty, locks and native ACK proof, and run the
  original writer/validator without input. Test 28 behavior cases both locally
  and with the complete original 0.4.31 controller.
- Bound review work to its fixed question and adequate checks. Deliver a report
  at the task boundary; do not reopen accepted work or repeat full regressions
  while waiting for unrelated closeout. Measure consumption latency separately
  from two successful continuing request/reply/consume/rearm rounds.

- Interpret reviewed legacy v2 inquiry files in CURRENT's own code after full
  original manifest, controller-member and issuer-scheduler verification. Never
  load old modules; preserve controller, UNKNOWN, terminal_pending, journals and
  a one-time complete original state/enrollment snapshot. Unknown old producers
  remain unsupported; file interpretation grants no transport or consume authority.
- Make start registration recoverable with preflight, a pinned PREPARING intent
  and a live-state registration-token commit. Distinguish uncommitted failure
  from COMMITTED_RECOVERY_REQUIRED; recover the exact transaction without a new
  episode. Verify original INDEX bytes and all five snapshot members, including
  absence. Preserve precommit state/channels and protect newer episode channels.
- Add authenticated `enroll-existing` with explicit episode and original raw
  enrollment SHA. Preserve the original controller and pending evidence; do not
  auto-migrate stale supervisor bindings, reopen finished work or tick on enrollment.
- Enforce formal ownership independently of file compatibility and continuation:
  same-supervisor reservations pause; a legitimate newer supervisor reservation
  for the original caller/workspace stops the old inquiry with FORMAL_OWNER_CHANGED.
  Only exact RELEASE removes a pause. TTL, heartbeat and missing markers grant
  no release or takeover. CURRENT harness marker/reservation operations share
  caller locks; nonblocking heartbeat rereads and never recreates a disarmed marker.
- Fix the residual single-quoted `; Codex alone` launch false positive without
  changing message text. Real adjacent CLIs, executable nested substitutions and
  wrappers remain checked. Earlier literal/heredoc fixes remain historical entries.
- Add `scripts/resume_task_no_input.py`: a read-only plan pins the
  original immutable controller, executor, task pack, exact notice and complete
  contiguous attempt history. `--apply --history-sha256` must match that plan.
  Only all-NO_INPUT/events=[] COMPOSE_OCCUPIED or RECEIVER_COMPACTING history,
  unchanged identity/native/lock/pack/payload/nonce and an empty ready composer
  allow one appended attempt through the complete original controller. Keep all
  original bytes; refuse input intent, UNKNOWN, queue, receipt or changed history
  and preserve any new draft. Ordinary automatic preflight stays bounded to two
  attempts; proven zero input is not exhaustion of the actual sending budget.
- Keep discovering a bound reply while a formal reservation pauses new requests.
  The pause survives ANSWERED and exact consumption until its matching RELEASE;
  only the original authenticated executor through its original controller may
  consume. File-only start is explicit and cannot enable transcript channels.
- Add a bounded read-only original-handshake ACK observer. Pin the original
  controller, timeout receipt, dispatch attempt and exact notice; write a separate
  observation without editing the timeout or sending input. Notice reception,
  body reading, ACK and business acceptance remain separate evidence.
- Clarify the supervisor's primary callback → acceptance/disarm → next-pack
  preparation → fresh preflight → dispatch chain. Keep 60-second reasks as a
  fallback, preserve episodes and prevent repeated dispatch while ANSWERED is
  unconsumed. File timers do not wake models. Measure accepted unique coverage,
  verified issue closure and stage-specific latency p50/p95 with sample counts;
  ask_count is no productivity score. Historical detected-to-consumed intervals
  establish neither pure idle time nor causation or live adoption.

Validation: the full offline suite passed 1,885 tests with zero failures or
errors. Registration, actual scheduler execution, automatic hooks, full body
reading and two real continuing request/reply/consume/rearm rounds each require
separate live evidence. Native delivery, nonblocking hooks, no-resend behavior
and Claude maintenance notice exposure retain the 0.4.31 contract.

## 0.4.31

- Add opt-in file context for a Claude executor that keeps waiting for a failed
  supervisor while an authorized successor notice is already queued. Verify the
  original attempt, authorization, complete body, queue record, transcript and
  live Claude process. The existing PostToolUse advisory exposes the body without
  sending input, interrupting tools, changing controllers or accepting tasks.
- Deduplicate successful exposure for 60 seconds and retain it after output
  failure. An exact executor read reply stops reminders; exposure and a file
  reply never manufacture native reception or business acceptance.
- Add isolated regression tests for the actual wrapper, foreign sessions,
  process reuse, changed evidence, concurrent-safe cursors and failed output.

## 0.4.30

- Prevent an unrelated legacy client with a reused PID from invalidating caller discovery because of a foreground record from the previous process. Accept legacy absence only for a fully validated, stable, optional record predating the current process and never observed for that process.
- Remove the global foreground-error veto: invalid or unavailable candidate records are excluded without granting argv fallback. Only the selected caller's recheck and actual duplicate matches can reject an otherwise valid selection; unrelated failures or thread switches do not contaminate its proof.
- Preserve required-observer, current-selection, exact-resume, unique-client, kernel/TTY/UUID and native-receipt checks. Do not change or delete foreground records, adopt the old thread or block ordinary tools and Stop.
- Cover current caller discovery, stale-thread rejection, legacy native binding, duplicate clients, required observers, record drift and the seen-process capacity boundary.

## 0.4.29

- Doctor checks both CURRENT-pinned launchd jobs, detecting a scheduler left on an older release. Installation and actual scheduled execution remain separate evidence.

- Serialize non-submitting handshake probes under the existing formal-delivery pane and target locks. Contention sends no input; replacement lock inodes stop further probe keys.
- Add regression coverage for foreign continuation text after a task paste: no Enter, no replay, no exact native receipt for mixed input.
- Document coordination with automatic continuation writers and evidence-preserving recovery when another sender has already submitted the mixed draft.

## 0.4.28 — Explicit continuing file inquiries

- Fix ASCII-clipped Claude cwd/summary false occupancy and preserve unrelated inherited inquiry attempts without blocking new authorized contact. Normal successor handshakes need no old-task settlement.
- Add explicitly authorized same-pane Codex native maintenance input with distinct pinned threads; terminal routes remain refused.
- Correct ARMED advisory wording and document successor latency attribution, original late-ACK reconciliation, and proactive disk recovery.

- Preserve explicitly authorized overall-task inquiries across WAITING replies and child-task closeout; default one-episode behavior remains unchanged.
- Require exact full-reply consumption with durable observed/consumed timestamps; pause on formal task reservation and resume on its release.
- Keep UNKNOWN input attempts, controller pins and nonblocking hooks; revoke on stop, expiry or changed authorization.
- Make the remaining-budget regression deterministic while retaining real watchdog tests.

# 0.4.27

- Preserve the authenticated foreground thread in public identity proof. Separate the actual hook thread, inherited selector and verified root/parent relationship; child hooks cannot consume the parent's inbox or acquire its ownership.
- Bound supervisor file-request context to eight items per tool event, rotate fairly, repeat unanswered reminders after at least 60 seconds, and commit cursors only after successful output flush. Root and child discovery stay separate and all workflow hooks remain nonblocking.
- Add an independently installed, explicitly enrolled file-only scheduler for authorized inquiries. Its 60-second job has a 256-enrollment cap, a 10-second sweep budget and nonblocking locks; it never sends, reads transcripts, migrates old controllers or wakes an idle model. Preserve pending attempts and unknown state across genuine episode transitions.
- Fix quoted Python heredoc prose containing subprocess imports and semicolons being misclassified as a Claude launch. Inspect executable arguments separately, including aliases, concatenation and shell wrappers; keep real terminal input guards.
- Align both skills and global guidance: new Codex input uses the original native thread once, never composer/Enter/Tab/follow-up queues. Keep installation, actual hook execution, file exposure, body reading, native reception and business acceptance separate. Use writable source for tests and support Python 3.10 test contexts.

# 0.4.26

- Send new Codex-bound messages, packs and callbacks directly to the original loaded thread through an authenticated control socket: active-turn steer or idle-turn start. Persist one native intent and fresh EOF fence; require exact new native user evidence. Never operate the composer, use the Tab queue, retry an uncertain request or replay historical callbacks.
- Recognize a measured ellipsis-clipped git branch at the physical end of a Claude footer row below matching composer borders. Preserve drafts, incomplete borders and unknown layouts.
- Restore authenticated, bounded file-request discovery through the nonblocking supervisor PostToolUse observer. Emit supported client context even if telemetry fails; never send input, settle tasks or block tools/Stop.
- Document mailbox-first reask handoff, the 540-second per-call limit and the absence of idle-supervisor wakeup. Keep historical pending attempts and receiver bindings unchanged.

# 0.4.25

- Classify explicit insufficient account balance as BILLING even with a login hint or HTTP 403. Preserve transport/budget precedence and generic authentication classification.
- Align supervision and configuration guidance with advisory-only workflow hooks, explicit report reconciliation and authorized 60-second reasks. No automatic callback settlement or forced turns.
- Preserve original controllers during shared upgrades and distinguish installed configuration from active-client adoption.

# 0.4.24

- Permit measured local SQLite inventory and disk-space document commands. Quoted semicolons followed by agent names remain data; real adjacent launches and executable Python still receive the original checks.

# 0.4.23

- Validate new task-pack wire format before creating a journal. A formal
  `NO_INPUT` rejection can be repaired only through the original controller,
  with the original attempt, pack, nonce and identity pinned; no old attempt is
  edited and the second input budget is durable.

# 0.4.22

- Accept the measured Claude layout that renders the model, cwd and elapsed
  fields on three separate footer rows. Unknown or incomplete footer layouts
  remain protected and cannot authorize input.

# 0.4.21

- Resolve native callers after shell exec optimization: a direct managed-daemon child supplies its kernel-observed thread selector and is rechecked for process drift.
- Keep native foreground, unique live client, TTY and UUID validation; a conflicting selector remains an error for that input operation only.
- Include Python 3.10 test compatibility, synthetic public footer fixtures and consistent non-blocking workflow documentation.

# 0.4.20

- Recognize measured Claude tool-count overflow and wrapped model/cwd/time footers without consuming drafts or active turns.
- Treat literal document heredocs and quoted searches as data; lexer failures alone no longer block ordinary tools.
- Preserve checks on actual raw terminal writers, including nested shells and malformed trailing quotes.
- Clarify native caller discovery and user-authorized cross-workspace successor communication in current guidance.

# 0.4.19

- Remove global workflow tool/Stop blocks, including closeout and reask loops; retain automatic invocation evidence.
- Resolve helper self through the native caller resolver instead of inherited daemon identity.
- Add user-authorized maintenance/successor communication without old-supervisor settlement, with durable single-input native receipts.
- Keep old callback journals, drafts, task acceptance and shared write scope separate.

# 0.4.18 — 2026-10-10

- Add a separate `successor-rebind-v1` artifact and guarded cross-workspace
  successor pair route. Ordinary same-workspace transport stays fail-closed;
  old task packs, callbacks, nonces and native journals remain immutable.
- Require explicit user authorization, independent supervisor-failure evidence,
  frozen old writers and fresh successor identity/receipt before any successor
  input. A fixed historical `codex resume` value is never required.
- Record the live-client adoption boundary: a native Claude `ConfigChange`
  receipt is a real settings-watcher diagnostic only and never substitutes for
  automatic `PreToolUse`/`Stop` adoption, native delivery, or task acceptance.
- Require a fresh inventory before reporting coverage. Shared-daemon reloads do
  not cover embedded Codex clients; an embedded client without a supported
  reload endpoint remains `UNVERIFIED` and its session is preserved.
- Preserve fail-closed transport when the caller's current cmux workspace cannot
  be joined to a live tree: process/MCP liveness is not a handshake or delivery
  receipt, and no input is permitted until the UUID proof is restored.

# 0.4.17 — 2026-10-10

- Remove the conversation-wide tool/Stop lock on unresolved task-hook identity,
  including enrolled native sessions. Preserve markers, frozen reports and
  callbacks; resume normal checks after discovery recovers.
- Retain kernel/workspace authentication at every terminal input. New and
  successor Codex supervisors use native foreground-thread selection; no
  particular resume command or predecessor session id is required.
- Include the 0.4.15/0.4.16 foreground discovery and task enrollment fixes in the
  public source, with isolated same-release imports and final session rechecks.
- Keep diagnostics, repair, bookkeeping and authorized solo work available;
  choose zero, one or two executors by current capacity and independent scope.

# 0.4.13 — 2026-10-10

- Compare complete static callback arguments instead of shell text when
  admitting the sole original NO_INPUT successor. Equivalent literal quoting,
  including unquoted Unicode paths without spaces, no longer causes rejection.
- Continue rejecting shell expansion, comments, control tails, wrappers,
  background execution and conflicting callback metadata. Original task,
  report, attempt, Python and controller bindings and the attempt budget remain.

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
