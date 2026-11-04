# Herdr receiver and caller evidence

An observed Herdr endpoint, pane, terminal and native session locate the receiver.
They do not authenticate the agent issuing a tool call inside that terminal.
Keep receiver observations, actual body reading, result production and authority
to send another message separate.

A live check on 2026-11-04 matched one original Claude main-session Bash call
and its result by session, tool-use ID, source assistant UUID and
`isSidechain=false`. The probe executed despite the host prompt command ending
with a status timeout. It deliberately returned exit 2 with no observation
errors, `branch_status=UNKNOWN` and `send_authorized=false`. This proves that
completed observation belonged to the main session; it does not authenticate a
future call before input. Preserve the original attempt and inspect its result
instead of resending after the host timeout.

The actual main-session Python and RTK processes both had
`CLAUDE_CODE_CHILD_SESSION=1`. Do not use that flag to classify a caller as a
fork. A real conversation-fork subagent was also observed with the parent's
session ID. PID, session ID, outer cmux UUID and inherited environment cannot
by themselves distinguish the current branch. Absence of an agent field is
not positive proof of a root caller.

Official Claude documentation checked on 2026-11-04 provides useful correlation
fields, with narrower guarantees than caller authentication:

- [`PreToolUse`](https://code.claude.com/docs/en/hooks#pretooluse) runs before a
  tool invocation and carries `tool_use_id`. The
  [common input fields](https://code.claude.com/docs/en/hooks#common-input-fields)
  describe `agent_id` for subagents and in-process teammates. `agent_type` is
  a type/name and can also occur with a top-level `--agent`.
- The [environment reference](https://code.claude.com/docs/en/env-vars#variables)
  documents `CLAUDE_CODE_SESSION_ID` in Bash, PowerShell and command-hook
  children. No ordinary Bash injection guarantee for
  `CLAUDE_CODE_TOOL_USE_ID` was found in the six official pages checked.
- A [conversation-fork subagent](https://code.claude.com/docs/en/sub-agents#fork-the-current-conversation)
  differs from [`--fork-session`](https://code.claude.com/docs/en/cli-reference#cli-flags),
  which creates a new session ID.
- The [hook reference](https://code.claude.com/docs/en/hooks#common-input-fields)
  says transcript writes are asynchronous. A pre-tool hook cannot assume that
  the current tool-call record is already present on disk.

A future caller adapter needs evidence of the actual native hook origin and
its association with this exact tool process and invocation. Documented IDs,
manually supplied hook JSON, environment values and a matching historical
transcript record are not signed capabilities. The current check does not
establish such an adapter. Keep unresolved branch identity unknown, retain file
results as file results, and do not label them native reverse-delivery success.

This limitation does not stop independent authorized implementation or artifact
work. It also does not transfer an existing task to another controller, justify
resetting an agent, or permit a fabricated foreground record.
