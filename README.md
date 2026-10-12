Current release: **0.4.30**. New Codex-bound input goes directly to the original native thread: active-turn steer or idle-turn start, with no composer or Tab queue. Uncertain requests are observed without resending. Workflow hooks remain nonblocking, and supervisor PostToolUse exposes authenticated file requests without sending or settling work. See the [delivery contract](references/verified-compose-delivery.md) and [request lifecycle](references/configuration-and-reasks.md).

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
