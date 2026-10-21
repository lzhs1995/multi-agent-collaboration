# 0.4.59: stable hook entry

Release-pinned command strings let an old Claude startup settings file and a
newly loaded global file register different commands for the same guard. New
immutable-release installations use one stable command per guard:
`~/.local/share/multi-agent-collaboration/bin/mac_hook_dispatch.py --hook NAME`.
The entry resolves CURRENT and verifies its pinned manifest, router and complete
local Python runtime dependencies on every invocation before importing them.
It execs the current release's Python/router with stdin
unchanged; the router preserves each guard's original route and exit status.

Install the stable entry before registering its command. Keep it after uninstall
or an installation rollback because an existing process may still call that
path. Rolling CURRENT back to an approved release without the router uses the
frozen legacy route. Unreadable or changed dispatch metadata is a diagnosed
no-op, never a successful adoption record. The private LAST-SKIP record contains
no hook payload. Do not equate exit zero with execution of the intended hook.

A pinned router that cannot import records `ROUTER_IMPORT_FAILED`, separately
from an unknown hook name. Doctor exercises the real dispatcher/router in an
isolated namespace and rejects diagnostic skips, even when they exit zero.
Explicit single-profile reconciliation also republishes an absent dispatcher
when the profile already uses the stable form. Preview never publishes; repair
does not change guardian registration, and differing existing dispatcher bytes
are preserved. A deliberate guardian pause stays under its original owner.

Successor lock refusal identifies the contended shared lock and path before
input. Bounded `holder_pids` observation lists open-file PIDs, which are advisory
and do not prove flock ownership. A failed or timed-out diagnostic changes no
lock, draft, input intent or original-controller reconciliation requirement.

The guardian also reconciles bounded, owned startup settings in magpie profiles
and cmux settings, using original-byte comparison and backups. It preserves
provider fields, foreign hooks and explicit `disableAllHooks`. Ordinary cmux
snapshots receive no hooks unless they already contained this project's hooks.
This is not discovery of every possible configuration root.

Installed files, a client loading them, and actual automatic execution remain
separate evidence. A process retaining an old release command in memory does
not acquire the stable entry retroactively. Do not claim full fleet adoption,
restart a conversation merely for a test, or edit an old immutable release to
conceal that limitation. Verify original-session hook provenance after a
supported client refresh; continue the original business task during rollout.

The reviewed complete 0.4.58 release remains a recognized file-scheduler issuer
and controller, including its explicit proactive enrollment. Its original
controller, identity, pending input, consumption authority and original issuer
range do not change. Installation does not enroll an old episode or make an
older issuer authoritative for 0.4.59. Existing three-state continuity, direct
delivery and draft protection contracts remain in force.
