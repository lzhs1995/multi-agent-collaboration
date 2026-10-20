"""One literal terminal paste, immediate Enter, and native marker receipts.

The caller owns the original journal and input lock. Busy work and compaction
are not admission gates. No draft is cleared or pasted twice. A single extra
Enter is permitted only after a pending receipt and visible residual/compaction.
"""
import time

from cmux_confirmation import annotate_error, observe_receipt, wait_budget

TRANSPORT = "terminal-direct-marker-v1"


def send_once(text, marker, *, recheck, emit, paste, key, observe, read_screen,
              wait_seconds=None):
    if not isinstance(marker, str) or not marker or marker not in text:
        raise ValueError("DIRECT_UNIQUE_MARKER_REQUIRED")
    seconds = wait_budget(wait_seconds)
    started = time.monotonic()
    operations = 0
    extra = False
    try:
        recheck()
        # Empty screen records that no composer inspection was required. The
        # caller captures/fsyncs the original transcript fence in this event.
        emit("PASTE_INTENT", "")
        recheck()
        operations += 1
        paste(text)
        emit("PASTED")
        emit("ENTER_INTENT", "")
        recheck()
        operations += 1
        key("enter")
        emit("ENTER_SENT")
        result = observe_receipt(observe, seconds, stop_when_queued=True)
        if not result.get("confirmed") and result.get("state") == "NATIVE_PENDING":
            # Observation here is solely for the extra-key budget, never for
            # initial admission. A queue already accepted input needs no key.
            screen = read_screen()
            emit("DIRECT_RECOVERY_OBSERVATION", screen)
            if marker in screen or "compacting" in screen.lower():
                result = observe()  # close the receipt race before one extra key
                if not result.get("confirmed") and result.get("state") == "NATIVE_PENDING":
                    emit("EXTRA_ENTER_INTENT", screen)
                    recheck()
                    extra = True
                    operations += 1
                    key("enter")
                    emit("EXTRA_ENTER_SENT")
                    result = observe_receipt(observe, seconds, stop_when_queued=True)
        return dict(result, transport=TRANSPORT, input_operations=operations,
                    terminal_input_operations=operations, submission_key="enter",
                    extra_enter=int(extra), retries=0,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 3))
    except BaseException as exc:
        raise annotate_error(exc, transport=TRANSPORT,
            input_operations=operations, terminal_input_operations=operations,
            extra_enter=int(extra), no_repaste=True)
