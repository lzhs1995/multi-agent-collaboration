# Bounded hook identity work

The original 2.5-second resolution budget remains. Descriptor reads stay fresh;
multiple writable rollouts still refuse. Three local optimizations reduce
repeated work without broadening task or message authority:

1. Signature validation hashes the entire executable at the beginning and end
   of each hook resolution. Intermediate calls reread the protected signature
   cache and current file/launcher identity, requiring the original pin. An
   updated signature cache cannot authorize a changed executable within the
   same resolution. The scoped state is discarded on success or failure;
   outside this scope every call still hashes the executable. An expired or
   failed closing digest prevents any successful result.
2. Native rollout reading searches backward for the same last complete
   nonblank record, retaining CR/LF boundaries, offsets, byte counts and
   partial-tail behavior. It still reads the bounded tail, verifies the
   selected JSON record, and rechecks the original observed record and FD.
3. A hook with authenticated embedded-client proof requests the exact client
   workspace tree twice. Generic transport and managed-daemon callers retain
   the global tree. Both identify calls, all process/foreground recollections
   and the final drift checks remain. No inherited workspace alone selects
   this narrower query.

SHA streaming uses a reused 256 KiB buffer and checks the common deadline
between chunks. Full-file digest validation at both resolution boundaries is
an explicit change from repeated full hashing at every internal check; it is
not a cross-invocation signature cache or a file-metadata-only authorization.

Manual live observer success proves only that the candidate can expose the
selected queued notices within the hook's overall timeout at that time. It
does not prove registration, trust, active-client loading or automatic output.
