# Darwin native FD inventory

Official-client foreground discovery on macOS reads fresh vnode descriptors
with the public `libproc` `proc_pidinfo(PROC_PIDLISTFDS)` and
`proc_pidfdinfo(PROC_PIDFDVNODEPATHINFO)` interfaces. This avoids repeatedly
starting `lsof` inside the existing 2.5-second hook identity budget.

The ctypes layouts follow the macOS SDK `sys/proc_info.h`: sizes 8, 24, 136,
152, 1176 and 1200 for the six structures, with the path at offset 176.
Runtime ABI checks reject an unsupported layout. A local C program compiled
against the installed SDK independently confirmed these sizes and offsets.
No compiler or generated binary is required at runtime.

The reader permits at most 8192 descriptors, allocates one spare slot, and
rejects a full, malformed or failed listing. Every listed vnode is read; a
failed or short vnode read rejects the observation. The descriptor list is
read again after inspection and must agree. No FD result is cached.
`FWRITE` determines writability; `FREAD` distinguishes update from write-only.
The original unique-writable-rollout selection, file/inode checks, metadata,
post-birth record and before/after process, ancestor and descriptor checks
remain in place. Multiple writers remain ambiguous, including two FDs for
the same file. Native errors never fall back to `lsof` on Darwin.

The existing lsof parser remains available on other platforms and for an
explicit read-only comparison. Native parity with lsof is tested against a
real fixture process on Darwin; mocked negative cases run on all platforms.
Manual observer diagnostics do not prove automatic hook loading or body
consumption. A faster descriptor read alone does not prove the complete
identity or observer call fits its budget under current host load.
