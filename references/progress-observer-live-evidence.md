# Local process STOP evidence and its limits

On 2026-11-04 the installed `fbea83dbdb25` progress component watched one
explicitly owned Python fixture that naturally exited after about 70 seconds.
The original CLI ran for 130 seconds without a mock clock or process reader.
Its three observations were LIVE, LIVE and ABSENT, separated by approximately
60.008 and 60.011 seconds. It recorded one process STOP and one
OBSERVER_STOPPED at the deadline. The observer exited normally and its final
coverage was UNKNOWN. The supervisor read the event journal and consumed the
specific STOP event. Ten scoped assertions passed.

This filled a concrete gap in two earlier observations where the watched
Claude process remained alive throughout the observation window. The earlier
windows already proved bounded observer termination; they did not demonstrate
target-process STOP detection. Their evidence and original controllers were
retained. The newer component was tested independently without migrating an
existing task or recreating either old attempt.

The new result proves local process-exit detection at the next scheduled scan.
It does not prove an agent completed its work, that all descendants exited,
that Word or a remote operation finished, or that a lease can be released.
The test sent no native notice and was not a pair of chapter teams. Its single
ABSENT sample is not evidence of repeated-absence or resume deduplication.
Reading the event file is separate from delivering and reading a peer message.

For a real task, bind its actual process or service handle and original
observer, then collect the task's own output and release evidence. If the
observer stops, report coverage as unknown. Keep event identity, journal
consumption, native notification and domain acceptance as separate facts.
