#!/usr/bin/env python3
"""Test-only driver: the real `executor_ready.run_loop` with a fixture bridge.

Named `..._executor_ready.py`-compatible on purpose: the Stop guard proves
liveness with `ps`, so a fixture loop must look like what it is -- this file's
own path carries `executor_ready.py` and the argv carries `persist`. Product
entrypoints never import it, and it never touches a real terminal.
"""
import sys
import time


class FixtureBridge:
    def pin_workspace(self, surface, **kwargs):
        return dict(caller_surface_uuid=kwargs.get('caller_uuid') or 'caller')

    def read_screen(self, surface, lines=200):
        return 'fixture screen\n'

    def pending_queue_holds(self, screen, marker):
        return False

    def compose_block_text(self, screen):
        return None

    def _looks_like_task_dispatch(self, text):
        return False

    def submit_text(self, surface, text, marker=None, reconcile_only=False):
        return dict(state='CONFIRMED')


def main():
    scripts, caller = sys.argv[1], sys.argv[2]
    sys.path.insert(0, scripts)
    import executor_ready
    executor_ready.run_loop(FixtureBridge(), 'surface:1', caller, 'surface:2', 'fixture-task',
                            interval=float(sys.argv[3]) if len(sys.argv) > 3 else 60.0,
                            poll=1.0, max_ticks=600, clock=time.time, sleep=time.sleep)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
