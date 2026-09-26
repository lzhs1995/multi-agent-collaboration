# Receiver input and delivery evidence

Before every agent message, read the bound UUID's current screen. `submit_text`
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
| No paste/Enter | Not submitted | Resolve the recorded receiver/input issue |
| Pasted/Enter, result unknown | Submitted, not confirmed | Inspect same UUID and marker, do not resend |
| Marker in pending queue | Received and queued | Wait for the original tool boundary |
| Marker followed by fresh activity | Receiver consumed input | Await its actual result |
| Reply/ACK | Response or possession of channel | Read the verdict; not automatic agreement |
| Report and bound callback | Reported completion | Independently verify artifacts and scope |

`DISPATCH_UNCONFIRMED` or helper exit 75 is not evidence of non-delivery. A later
reply or actual artifact may resolve the same original dispatch. Preserve both
observations; do not replace the failure record with a retroactive success.
Only a visibly pending, idle paste may receive the bridge's one extra Enter;
it is never repasted. Queue/unknown outcomes do not take that retry path.
An explicit queue entry for this marker also takes priority over fresh activity
from an earlier task; such activity cannot turn queued delivery into consumption.

Calls without a marker now report `submitted=true, confirmed=false` after input.
Formal task/callback paths already carry markers. This makes unmeasured
consumption explicit and avoids turning a successful send into acceptance.
An occupied composer is preserved by default; explicitly authorized force-compose
still cannot clear active/queued work. Future UI shapes need a measured fixture
and an updated detector; a historical screenshot is not current liveness.

Tests use synthetic screens and count actual mocked send/key calls. They verify
zero sends to a shell, preserved user input, one send for queued delivery, and
the distinction between current and historical UI. They do not certify another
terminal, restart a peer or change its model. Cross-agent review records who
actually responded; unavailable peers' old evidence is not new consensus.
