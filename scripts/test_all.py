#!/usr/bin/env python3
"""Offline test entrypoint; capture fixture chatter without hiding failures."""
import contextlib
import io
from pathlib import Path
import sys
import unittest

if __name__ == "__main__":
    chatter = io.StringIO()
    with contextlib.redirect_stdout(chatter), contextlib.redirect_stderr(chatter):
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_*.py")
        result = unittest.TextTestRunner(stream=sys.__stderr__, verbosity=1).run(suite)
    print(f"OFFLINE_TESTS total={result.testsRun} failures={len(result.failures)} errors={len(result.errors)}")
    sys.exit(not result.wasSuccessful())
