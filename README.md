Current source: **0.4.34**. Late handshake reconciliation accepts a Claude native assistant record containing one exact ACK text block alongside structurally valid reasoning blocks. Extra text, tool blocks, malformed metadata and any ACK whitespace or nonce difference remain rejected; reasoning contents are never emitted in reconciliation results or archives. The original supervisor and complete original controller remain required. See [late ACK reconciliation](references/late-handshake-ack.md).

File scheduling retains the fully verified 0.4.33 v3 producer and v2 issuer,
including its historical enrollment of the 0.4.32 controller. An older issuer
cannot claim support for a future controller. No registration, consumption or
transport authority is inferred from compatibility.
The 40 ACK behavior tests pass locally and through the original 0.4.32 controller.
The final offline suite passes all 1,912 tests with no failures or errors.

The Claude footer parser recognizes the HUD's measured input/cache token breakdown below complete composer borders. It preserves draft text and continues to refuse ordinary input during compaction or reconnect. New Codex-bound input goes directly to the original native thread: active-turn steer or idle-turn start, with no composer or Tab queue. Uncertain requests are observed without resending. Workflow hooks remain nonblocking, and supervisor PostToolUse exposes authenticated file requests without sending or settling work. An explicitly enrolled Claude maintenance notice can expose its verified original queued body at a tool boundary; it never changes the original transport receipt. See the [delivery contract](references/verified-compose-delivery.md) and [request lifecycle](references/configuration-and-reasks.md).

Version **0.4.33** also retains file scheduling for the fully verified 0.4.32
v3 producer and v2 issuer, including its original registrations of reviewed
older controllers. Complete manifest and descriptor checks preserve the
original controller and pending evidence; consumption remains executor-owned.
It also provides original-supervisor ACK reconciliation for a handshake rejected
after pasting without submission. Exact native ACK proof and the original
controller remain required; see [late ACK reconciliation](references/late-handshake-ack.md).
The 35 ACK behavior tests pass locally and through the original 0.4.32 controller;
the final combined offline suite passed 1,906 tests with zero failures or errors.

Version **0.4.32** adds CURRENT's reviewed cross-release v2 file interpreter,
recoverable registration transactions and explicit registration of an existing
episode using its original enrollment SHA. The file scheduler preserves original
controllers, UNKNOWN and pending evidence without loading old release code or
sending input. Formal reservations pause same-supervisor inquiries; a valid
newer reservation by the legitimate new supervisor ends the old supervisor's
inquiry, even for an unsupported old producer. Only exact RELEASE clears a
same-supervisor pause. That pause blocks new requests while retaining discovery
of an already-bound reply; only the original authenticated executor and its
original controller may consume it. Explicit `start --file-only` rejects
transcript channels.
This version also fixes a single-quoted `; Codex alone` launch false positive
while retaining checks on executable shell code. A separate explicit zero-input
recovery entrypoint defaults to a read-only history plan; applying it pins the
complete history SHA and appends one attempt through the original controller
only when every earlier attempt proves no input. See [SKILL.md](SKILL.md) for
the exact evidence and readiness checks. Its full offline suite passed 1,885
tests with zero failures or errors. Installation and active-client adoption need
their own evidence. See the [cross-release file contract](references/configuration-and-reasks.md#cross-release-file-contract).

The bounded handshake observer preserves the original timeout and send attempt
while reporting a late ACK without input. Validate registration, real scheduler
execution, automatic hooks, full body reading and two real continuing rounds
separately; offline tests and file ticks do not establish those live outcomes.

Caller discovery now distinguishes a validated optional foreground file from an
earlier use of the same PID. It preserves current-process, required-observer and
unique-client checks; it does not rewrite old records or select their threads.
See [PID reuse](references/shared-daemon-caller.md#reused-pids-and-optional-foreground-files).

# Multi-Agent Collaboration

New and successor Codex supervisors use their active
native foreground thread. Caller discovery failure cannot lock ordinary tools or
Stop; task evidence stays intact and terminal input still verifies both endpoints.
See [recovery without a session lock](references/native-caller-inventory.md#recovery-without-a-session-lock).

Evidence-bound collaboration for Codex and Claude Code in macOS cmux.
中文：复用完整上下文会话，明确分工，以真实报告和主动 callback 完成协作；
压缩故障及时干预，不以重开会话、假 ACK 或反复轮询代替恢复。

New messages use bounded single-line notices for pinned
bodies; formal packs and callbacks retain their dedicated task-bound routes.
Native RPC acceptance, Enter or Tab only requests submission. Reception requires the complete exact
payload in a new record of the bound receiver's native journal. See the
[delivery contract](references/verified-compose-delivery.md).

The existing Codex control socket must authenticate as the current user and expose
the exact loaded receiver thread. Persist one native input intent before the only
steer/start request. Never fall back to paste/Tab after failure or uncertainty.
Claude-bound input retains the protected terminal route; old queued callbacks
remain under their original controller for read-only reconciliation.

## Install

Requirements: Python 3.10+, macOS, cmux, and existing CLI agent sessions.
No provider credentials or agent subscriptions are included.

```sh
git clone https://github.com/lzhs1995/multi-agent-collaboration.git
cd multi-agent-collaboration
git checkout main
python3 scripts/manage_install.py install
python3 scripts/manage_install.py install --apply
python3 scripts/manage_install.py doctor
```

Install defaults to dry-run. It links this checkout into both clients' skill
directories and merges only this project's hooks. Existing foreign skill
directories are refused, never overwritten. Keep the checkout at its installed
path. For a live shared installation, stage a complete immutable release and
coordinate the upgrade with an installation lock, compare-and-swap checks and
rollback snapshots. The basic installer is not a transaction spanning the whole
installation. Preserve in-flight attempts under their original controller;
independent authorized work need not wait. Verify active-client adoption
separately, and never replace an agent session to upgrade its configuration.

```sh
python3 scripts/manage_install.py uninstall --apply
```

Uninstall removes only entries and links owned by this checkout. JSON backups
remain beside the original configs and may contain credentials: keep them private.
Other settings and hooks survive. Use `--home /absolute/sandbox` to test installation
without touching real client configuration.

For explicitly authorized 60-second executor follow-ups, add `--executor-reask`
to installation and doctor. To preserve owned hooks across cc-switch changes,
use the single [configuration guardian](references/configuration-and-reasks.md)
with a complete immutable release and its pinned CURRENT manifest. It repairs
hook registrations only; Markdown guidance does not itself register a hook, and
configuration repair does not prove a running client has loaded it.

## Use

Ask the supervisor to use `$multi-agent-collaboration`, then provide the objective
and designated existing executor. Both agents must read SKILL.md. The harness
binds their identity and task; scripts do not log in or start a replacement agent.

```sh
python3 scripts/mac_harness.py --help
python3 scripts/mac_harness.py preflight --task-id example-review \
  --artifact-root /absolute/path/to/example-review --executor-surface surface:7 --executor claude
```

Replace the example surface with a freshly verified binding. Do not paste task
prompts manually: use the finalized task-pack and callback entrypoints described
in [SKILL.md](SKILL.md). Public defaults preserve the current model and refuse
unknown compose text. Machine-specific preferences belong in local task settings.

The next-task chain is callback → independent acceptance/exact disarm → prepare
the next authorized pack → fresh live preflight → guarded dispatch. Explicit
60-second reasks are a fallback; file timers cannot wake an idle model.
ANSWERED awaiting consumption does not authorize repeated dispatch or a replacement
episode. Track accepted unique coverage, verified issue closure and actual stage
latency p50/p95, with sample counts and pending observations; ask_count is only a
diagnostic. Reply-detected-to-consumed time alone proves neither business idle time
nor its cause. See [dispatch cadence and metrics](references/configuration-and-reasks.md#dispatch-cadence-and-metrics).

## Verification And Boundaries

```sh
python3 scripts/test_all.py
```

Tests use isolated files and mocked agent surfaces, without sending live prompts.
Offline coverage is not certification of every cmux/client version. `doctor`
proves configured hooks can execute benign payloads; a real task still needs the
current-session handshake and current-task guards. Linux is an offline-test target,
not a live UI transport. There is no Windows/tmux or ACP adapter in this release.

See [lessons](references/lessons.md), [security](SECURITY.md), and
[version history](CHANGELOG.md). MIT; cmux, Codex and Claude remain separate products.
