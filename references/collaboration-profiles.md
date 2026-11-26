# New-task profiles and compatibility

1. Read an existing task registration first. Follow its exact controller and
   original attempts even if its agent now runs inside Herdr.
2. For an unregistered new task, choose the route for the actual receiver. A
   target endpoint or explicit receiver profile takes priority over the sender's
   environment. Without either hint, `HERDR_ENV=1` suggests Herdr and otherwise
   cmux. These are suggestions, not enrollment, live identity proof or an
   instruction to launch peers.
3. A new task can explicitly pin `schema: collaboration-profile-v1`, `task_id`
   and `profile: herdr|cmux` in its own registration. Never add those fields to
   migrate an old task. `scripts/collaboration_profile.py route` is read-only.
   It accepts `--target /absolute/target.json` (with `endpoint.transport`) or
   `--receiver-profile herdr|cmux`. An existing registration always wins. A
   registered new profile conflicting with the receiver is returned unchanged
   with an explicit conflict and nonzero exit; an older registration keeps its
   original controller and ignores receiver override hints. No registration is
   rewritten.

For an explicitly authorized outer cmux caller inspecting an existing local
Herdr receiver, `check-herdr --session <name> --target /absolute/target.json`
uses the same named selector for official status and agent-list commands. It
requires a running compatible server, checks its actual socket and session name,
then verifies the pinned pane, terminal and native session. It never starts a
server or sends input, and does not require a fabricated `HERDR_ENV=1`.
Without `--session`, the helper retains the inherited Herdr environment path
and also verifies the actual status socket. Each read has a finite timeout;
failure never retries or falls back to a different server. This helper supports
local named sessions only: `--machine` forwarding is not implemented. A machine
endpoint needs a distinct machine identity as well as its remote socket path.

The official Herdr skill's internal-caller default does not cancel the user's
explicit authorization to coordinate a designated existing external target.
Use only selectors supported by the installed CLI and retain the real environment.
A selector addresses the receiver; it does not authenticate the caller or replace
an existing task's dispatch, resource or experiment checks. Ordinary authorized
contact and formal task execution remain separate operations.

Run these helpers as `python3 -B scripts/collaboration_profile.py ...`, and use
`-B` for Python processes importing them. The module disables bytecode for later
imports, but cannot prevent importlib from caching its own code before its body
runs. Do not delete an old release's cache to manufacture an integrity pass.

The [common contract](collaboration-contract-core.md) is maintained once here.
Herdr tasks read [the Herdr skill](../skills/herdr-collaboration/SKILL.md).
cmux tasks read [the outward cmux skill](../skills/cmux-collaboration/SKILL.md).
For chapter teams inside separate workspaces, read
[nested research coordination](nested-research-coordination.md). The inward and
outward skills are complementary workflow layers; select each delivery edge for
its actual receiver, rather than forcing every project message through one profile.

## Installation and hooks

Use separate complete release trees for new profiles. Do not edit immutable
installed files. Keep the current global dispatcher and `CURRENT.json` unchanged
while old tasks depend on them: the dispatcher selects CURRENT on every call,
not a task's historical release. Merely changing registration cannot fix that.

Existing hooks already filter participants by native session ID. A shared outer
surface does not by itself merge hook enrollment. Do not add a global
`HERDR_ENV=1 => exit` bypass. New task-specific configurations may add a separate
SessionStart context selector while retaining all foreign hooks. It must output
guidance only, never send input, enroll tasks, force continuation or accept work.
Current-client loading must be observed separately from installing a config.

Unenrolled Herdr sessions may still run installed legacy advisory hooks. This
release does not claim to remove their process/hash overhead. Consolidating that
dispatcher requires its own measured change and regression evidence.

## Chapter and whole-document coordination

For one cmux workspace per chapter with an inner Herdr team, see
[nested research coordination](nested-research-coordination.md). It defines the
chapter/senior-supervisor layout and optional workflow-mode metadata, not a new
runtime profile or automatic scheduler. The common contract remains single-source.

Herdr supports workspace coordination on the same server. Use its native route
where available. Across servers, discover with an explicit supported selector
and preserve that selector on every command; `w1:p1` is not globally unique.
cmux remains appropriate for independent terminals outside Herdr. Never inject
text into a cmux surface hosting a multiplexer and assume a particular inner pane
receives it. A verified direct native-session API is a separate possible route.
