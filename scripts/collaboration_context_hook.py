#!/usr/bin/env python3
"""Optional SessionStart guidance. No global config writes or task enrollment."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get('hook_event_name') != 'SessionStart':
            return 0
        target = (ROOT / 'skills/herdr-collaboration/SKILL.md' if
                  os.environ.get('HERDR_ENV') == '1' else ROOT / 'SKILL.md')
        text = ('For an existing task retain its registered controller and original attempts. '
                'Only for new requested collaboration, read ' + str(target) +
                '. This context does not enroll, send, accept or authorize new tasks.')
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'SessionStart', 'additionalContext': text}}))
    except (ValueError, OSError, AttributeError):
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
