#!/usr/bin/env python3
"""Validate, append, submit or reconcile an authorized successor handshake."""
import argparse
import json
from pathlib import Path

import successor_rebind
from cmux_evidence_io import read_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "append", "submit", "reconcile"))
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--now", type=float)
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--marker")
    parser.add_argument("--wait-seconds", type=float, default=3)
    args = parser.parse_args()
    try:
        value = json.loads(read_bytes(args.artifact))
        if args.command == "validate":
            result = successor_rebind.validate_artifact(value, now=args.now)
        elif args.command == "append":
            if args.output is None:
                raise successor_rebind.SuccessorRebindError("SUCCESSOR_REBIND_DENIED: --output required")
            result = successor_rebind.append_record(args.output, value, now=args.now)
        else:
            if args.now is not None:
                raise ValueError("--now is only a validation/append diagnostic; live transport uses real time")
            if args.text_file is None or not args.marker:
                raise ValueError("--text-file and --marker required for submit/reconcile")
            import cmux_bridge
            import successor_native_delivery as delivery
            text = read_bytes(args.text_file, limit=1024 * 1024).decode("utf-8")
            if args.command == "submit":
                result = delivery.submit(cmux_bridge, value, text, marker=args.marker,
                                         wait_seconds=args.wait_seconds)
            else:
                result = delivery.reconcile(cmux_bridge, value, text, marker=args.marker)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"confirmed": False, "state": "ERROR", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
