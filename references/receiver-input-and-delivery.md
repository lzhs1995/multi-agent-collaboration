# Receiver input and delivery evidence

Current delivery and recovery rules are defined in
[verified native delivery](verified-compose-delivery.md). Screen inspection protects
input ownership; only the original bound native record proves reception.

New Codex-bound messages use the existing receiver thread's authenticated native
control socket: one turn/steer for its fresh active turn, or one turn/start for
the idle original thread. Persist NATIVE_INPUT_INTENT, the exact request and a
fresh EOF fence first. No composer read, paste, key or follow-up queue is involved.
An uncertain result permits original-record observation only, never a retry.
Historical queued attempts stay under their original controller without replay.

Before Claude terminal input, read the bound UUID's current screen. `submit_text`
requires a current agent composer/provider footer or its observed empty prompt.
A bare zsh/bash/PowerShell prompt after agent exit overrides historical agent
output. A fancy shell prompt sharing an agent glyph remains UNKNOWN without
current UI evidence. SHELL/UNKNOWN send no text or keys; force-compose does not
bypass this check. Inspect the original session rather than replacing it.

The provider footer must belong to the bottom input area; unknown trailing
content overrides a stale footer or virtual suggestion. During authorized
compose clearing, recheck receiver type and active/queued work after every
screen read before sending another key. If the agent exits after Escape,
no subsequent clear, cancel or deletion key may reach the shell.

The low-level `send_text` is raw terminal input and is not an agent dispatch API.
Use it for a shell command only when that exact shell action is authorized and
the receiver is verified. Running a shell command does not restore a model TUI.

| Evidence | Meaning | Next action |
|---|---|---|
| Recorded zero-input refusal | Not submitted | Resolve the original receiver/input issue |
| Paste or key with no native proof | Unconfirmed | Preserve the original attempt; do not resend |
| UI queue or Claude `queued_command` | Pending, not received | Bounded read-only observation of the same attempt |
| New exact native user after original input-intent EOF fence | `NATIVE_RECEIVED` | Record reception; execution/acceptance remain separate |
| Reply/ACK | Response or channel possession | Read the actual verdict; not delivery or agreement proof |
| Frozen report | Report available | Supervisor independently verifies scope and artifacts |

`DISPATCH_UNCONFIRMED` or helper exit 75 is not evidence of non-delivery.
Preserve the original observation even if a later exact native record permits
reconciliation. Unknown/queued attempts are not repasted. For historical terminal
attempts, a complete stable original draft may use only the original controller's one shared additional-key
budget; recovery intentions consume it before input. There is no separate Tab
allowance. Missing original binding/fence cannot be added retrospectively.

New Claude terminal input uses one supported Enter after the entire original
draft and structure are stable. New Codex native input has no key or resend budget.
A historical queue entry remains pending. Screen activity, RPC acceptance, prompt
echo, empty compose, ACK or a marker alone cannot confirm reception.

An occupied composer is preserved by default; explicitly authorized force-compose
still cannot clear active/queued work. Future UI shapes need a measured fixture
and an updated detector; a historical screenshot is not current liveness.

Tests use synthetic screens and count actual mocked send/key calls. They verify
zero sends to a shell, preserved user input, one paste for queued delivery, and
the distinction between current and historical UI. They do not certify another
terminal, restart a peer or change its model. Cross-agent review records who
actually responded; unavailable peers' old evidence is not new consensus.

## 2026-10-05: Suggestion-like text preserves draft ownership

Screen text alone cannot distinguish a product suggestion from a typed draft. Treat visible `continue`, `/compact`, review requests and handshake suggestions as occupied, including with an auto-compact banner. Only an actually empty editor or the exact standard empty placeholder passes the empty-editor predicate. Preserve the first typed row and payload bullets; trim recognized footer rows only. Normal dispatch must refuse with zero paste/key operations. This source regression fix does not authorize force clearing or imply a running client has reloaded.
