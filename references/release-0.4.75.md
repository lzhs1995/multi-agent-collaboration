# Exact reply consumption after the original Claude session restarts

An original inquiry can retain an ANSWERED reply whose consumer PID has exited.
Editing that PID would invalidate its identity, registration and controller
binding. The separate `consume-resumed` command consumes that one exact file
reply through a freshly authenticated later process of the same native session.
It retains the old identity and enrollment instead of replacing them.

The command must come from the complete approved CURRENT release. Its actual
caller must be a tool of the resumed original Claude. The old PID must produce
the kernel's affirmative ProcessExited result; an alive or recycled PID,
timeout or permission failure refuses. The new process must have the same
executable and file identity, native session, surface, workspace, pane and TTY.
The original transcript device/inode and historical byte boundary remain pinned.
Resume argv, inherited selectors and another model ancestor cannot substitute
for native session registration or actual caller ancestry.

First FULL-read the original reply. Its canonical body SHA is different from
the raw mailbox file SHA. Run the preview through the current release:

```sh
"$PYTHON" -BE "$CURRENT_SOURCE/scripts/executor_reask.py" consume-resumed \
  --caller-uuid "$ORIGINAL_CLAUDE_UUID" \
  --original-controller "$ORIGINAL_SOURCE" \
  --reply-sha256 "$CANONICAL_REPLY_SHA"
```

Preview changes no files and does not create an episode or lock. Its plan pins
the complete approved release, exact raw state identity/hash, original controller,
reply, session and resumed process. Review that plan and use its exact SHA:

```sh
"$PYTHON" -BE "$CURRENT_SOURCE/scripts/executor_reask.py" consume-resumed \
  --caller-uuid "$ORIGINAL_CLAUDE_UUID" \
  --original-controller "$ORIGINAL_SOURCE" \
  --reply-sha256 "$CANONICAL_REPLY_SHA" \
  --apply --plan-sha256 "$PREVIEW_PLAN_SHA"
```

Apply rechecks CURRENT, original raw state, reply file bytes/identity, native
caller and continuing authority under the existing episode lock. A changed
preview cannot silently become an apply. Preserve it and make a new read-only
preview. Stop, revocation and expiry retain priority. A stop appearing at the
consumption boundary is reported as not consumed rather than successful work.

The durable receipt contains the exact original state UTF-8 bytes, original
identity and reply, the resumed caller proof and reviewed plan SHA. Consumption
uses the original lifecycle: a continuing reply rearms only the already
authorized task; a noncontinuing reply becomes CONSUMED. Old pending/UNKNOWN
input, enrollment, controller and formal reservations keep their owners. The
operation sends no input, creates no ask and releases or dispatches no task.
Subsequent file scheduling may issue a new marker under its original authority.

A post-replace directory-sync failure reports COMMITTED_SYNC_UNKNOWN with the
visible path and candidate SHA. Do not assume no write occurred. If the original
receipt is visible and the state is still ANSWERED, verify that exact receipt
and retry the same reviewed plan. The retry rechecks receipt scope and bytes,
syncs its directory without rewriting it, and keeps the original consumption
timestamp. If the episode state itself became visible, the existing
`confirm-sync --candidate-sha256` command confirms only that exact state file;
it never recreates or rewrites consumption. A completed reply refuses another
apply, preventing a second rearm.

Ordinary `consume` retains its original strict process binding. This release
does not renew old identities, change automatic hooks, wake an idle client or
prove that every future inquiry will progress. Each later resumed consumption
requires its own exact reply and plan. Offline CLI/file controls, installation,
actual native consumption and automatic client adoption are separate evidence.
