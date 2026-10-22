# Bounded native target observation

Version 0.4.64 narrows process discovery from all user processes to the exact
TTY returned by the authenticated target tree. The inventory must contain valid
pid, uid, tty and command columns. Foreign UID rows are filtered before kernel
reads. Malformed rows, duplicate PIDs and differing TTY columns are refused.
The inventory only discovers candidates: kernel UID, birth, executable, native
installation, session, surface, workspace, fresh TTY and stable rereads still
identify the original process. Multiple matching native clients remain refused.
Login shell labels keep the existing returned-to-shell diagnostic.

Discovery and per-PID TTY reads share at most ten seconds for one observation.
Each command has at most five seconds and two attempts. A shorter enclosing
identity budget wins; nested TTY checks cannot renew it. Only a read-only ps
TimeoutExpired can retry. Partial output is discarded. Non-timeout exit,
identity failure and ambiguous inventory never retry. Ordinary require_bound
and authorized cross-workspace successor require_bound use the same bounded
TTY observer. This adds no retry for input, registration, callbacks or unknown
prior attempts. A zero-input diagnostic covers only that process observation,
not the earlier operations of its caller.

A new continuing inquiry must use explicit proactive-hook and reservation
authority. Reusing the same binding with different proactive-hook authority
now refuses before state mutation and points to exact-preview register-successor
for a separately authorized scope. Missing legacy false stays absent; unchanged
true remains unchanged. Installing this release does not enroll old episodes.

The complete reviewed 0.4.63 file producer stays pinned. Old controllers,
pending messages, shared writers and callback ownership retain their original
authority. Source controls and independent review do not establish current
client loading, live successor registration, two consume/rearm rounds or
measured task efficiency. Those require their own original-session evidence.
