"""One literal terminal paste, immediate Enter, and native marker receipts.

The caller owns the original journal and input lock. Busy work and compaction
are not admission gates. No draft is cleared or pasted twice. A single extra
Enter is permitted only after a pending receipt and visible residual/compaction.
"""
import math
import re
import time

from cmux_confirmation import annotate_error, observe_receipt, wait_budget

TRANSPORT = "terminal-direct-marker-v1"


def _screen_regions(screen):
    rows = screen.splitlines()
    prompts = [i for i, row in enumerate(rows) if re.match(r"^\s*[›❯>](?:\s|$)", row)]
    if not prompts:
        return rows, ""
    index = prompts[-1]
    return rows[:index], "\n".join(rows[index:])


def is_compacting(screen):
    """Inspect the latest activity row, excluding the current input body."""
    before, _ = _screen_regions(screen)
    activities = [row.strip() for row in before
                  if re.match(r"^\s*[•·⏺✻✢✳✶✽◐◑◒◓](?:\s|$)", row)
                  and not re.search(r"Queued follow-up|Messages to be submitted", row, re.I)]
    rows = activities[-1:] if activities else [row.strip() for row in before]
    return any(re.match(r"(?:[•·✻✢✳✶✽◐◑◒◓]\s+)?Compacting\b"
        r"(?:\s+(?:context|conversation))?(?:…|\.\.\.)?(?:\s*\([^\n]*\))?", row, re.I)
        for row in rows)


def residual_marker(screen, marker):
    # Recovery only: a marker in chat history does not authorize submitting a
    # different draft. No full composer parser/stability or initial-send gate.
    return marker in _screen_regions(screen)[1]


def recover_once(text, marker, *, original, recheck, emit, key, observe,
                 read_screen, wait_seconds=None):
    """Spend an unused extra Enter on the original current-contract attempt.

    The caller holds its original locks and persists emit before every key.
    A deferred observation spends no budget; an uncertain key spends it forever.
    No paste, original-screen comparison, new fence or native RPC is permitted.
    """
    seconds = wait_budget(wait_seconds)
    binding = original.get("native_binding") or {}
    if (binding.get("receipt_contract") != "native-user-marker-v1"
            or binding.get("receipt_marker") != marker or not marker or marker not in text):
        raise ValueError("DIRECT_ORIGINAL_MARKER_CONTRACT_REQUIRED")
    events = original.get("events", [])
    phases = [event.get("phase") for event in events]
    if (any(phases.count(p) != 1 for p in
            ("PASTE_INTENT", "PASTED", "ENTER_INTENT", "ENTER_SENT"))
            or any("TAB" in str(p) or p in ("EXTRA_ENTER_INTENT", "EXTRA_ENTER_SENT",
                    "NATIVE_INPUT_INTENT") for p in phases)):
        raise ValueError("DIRECT_RECOVERY_BUDGET_UNAVAILABLE: reconcile original")
    operations = 0
    try:
        recheck()
        result = observe()
        if not result.get("confirmed") and result.get("state") == "NATIVE_PENDING":
            screen = read_screen()
            emit("DIRECT_RECOVERY_OBSERVATION", screen)
            if is_compacting(screen):
                result = dict(result, extra_enter_deferred="COMPACTION_STILL_ACTIVE")
            elif not residual_marker(screen, marker):
                result = dict(result, extra_enter_deferred="MARKER_NOT_VISIBLE")
            else:
                result = observe()
                if not result.get("confirmed") and result.get("state") == "NATIVE_PENDING":
                    recheck()
                    emit("EXTRA_ENTER_INTENT", screen)
                    recheck()
                    operations = 1
                    key("enter")
                    emit("EXTRA_ENTER_SENT")
                    result = observe_receipt(observe, seconds, stop_when_queued=True)
        return dict(result, transport=TRANSPORT, recovered="enter" if operations else "observed",
                    input_operations=operations, terminal_input_operations=operations,
                    extra_enter=operations, retries=0)
    except BaseException as exc:
        raise annotate_error(exc, transport=TRANSPORT, input_operations=operations,
            terminal_input_operations=operations, extra_enter=operations, no_repaste=True)


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
            recovery_started = time.monotonic()
            # Initial sending never waits for compaction. Only the single
            # recovery key waits: pressing it during compaction can swallow it.
            for index in range(math.ceil(seconds) + 1):
                if not is_compacting(screen):
                    break
                result = observe()
                if result.get("confirmed") or result.get("state") != "NATIVE_PENDING":
                    break
                left = seconds - (time.monotonic() - recovery_started)
                if left <= 0 or index == math.ceil(seconds):
                    break
                time.sleep(min(1.0, left))
                screen = read_screen()
                emit("DIRECT_RECOVERY_OBSERVATION", screen)
            if is_compacting(screen):
                result = dict(result, extra_enter_deferred="COMPACTION_STILL_ACTIVE")
            elif residual_marker(screen, marker):
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
