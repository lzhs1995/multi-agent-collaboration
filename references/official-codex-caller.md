# Official Codex caller compatibility

The maintained profile recognizes the selected, OpenAI-signed
`codex-aarch64-apple-darwin` executable. Its native process path must resolve to
the same file as `~/.local/bin/codex`; argv, file identity, protected signature
cache, tool ancestry, workspace, surface and TTY remain checked. A renamed or
unselected executable does not gain authority from its filename.

This compatibility implementation is carried forward unchanged from the
original 0.4.84 controller. Existing task controllers and attempts retain their
original files. Installing this profile does not migrate them.

When the optional original foreground observer is absent, the selected official
client can prove its foreground thread through one live writable rollout file
descriptor. The reader binds the inode, original CLI user-session metadata and
a complete record written after the process started. A native token-usage
record must name that same thread in both session fields. Malformed or required
original observer evidence cannot fall back to this reader.

Multiple writable rollout descriptors are still ambiguous, including completed
children whose writers remain open. This change does not fix that limitation or
authenticate a Herdr Claude branch. For an already registered task requiring
this identity contract, use its existing external executors. Do not spawn a
child inside the identity-bearing client and then ask the user to restart it as
routine recovery. A future multi-thread adapter needs native invocation evidence;
filtering descriptors by an inherited selector is not such evidence.

The signature hot path only reads an existing protected cache and checks the
actual executable digest. An update requires an explicit signature-cache refresh
through `scripts/official_signature_cache.py refresh`; never fabricate a cache
record or change the required signature to make admission pass. The signature
cache protects against other users and accidental changes, not a malicious
process running as the same user.

Run interactive `codex`, `codex resume`, `claude`, and `herdr` directly in their
real terminal. `rtk proxy` captures stdout/stderr and is unsuitable for these
interactive launches. It remains usable for noninteractive diagnostics.

Verify actual caller admission and task preflight separately. A successful
`cmux_agent_adapter.py self` is not message delivery, application execution,
resource release, or scientific acceptance.
