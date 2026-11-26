# New-task profiles and compatibility

1. Read an existing task registration first. Follow its exact controller and
   original attempts even if its agent now runs inside Herdr.
2. For an unregistered new task, `HERDR_ENV=1` suggests the Herdr profile; otherwise
   use cmux when the task calls for terminal collaboration. This is a suggestion,
   not enrollment or an instruction to launch peers.
3. A new task can explicitly pin `schema: collaboration-profile-v1`, `task_id`
   and `profile: herdr|cmux` in its own registration. Never add those fields to
   migrate an old task. `scripts/collaboration_profile.py route` is read-only.

The [common contract](collaboration-contract-core.md) is maintained once here.
Herdr tasks read [the Herdr skill](../skills/herdr-collaboration/SKILL.md).
cmux tasks read [the cmux profile](cmux-collaboration-profile.md).

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
