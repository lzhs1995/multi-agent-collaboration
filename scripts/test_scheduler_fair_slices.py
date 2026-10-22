"""Discriminating bounded scheduling controls in an isolated private registry."""
from contextlib import contextmanager
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import uuid

import executor_ready as ready
import executor_reask as reask
import executor_reask_scheduler as scheduler
import test_executor_reask_scheduler as fixtures


class VirtualBudget:
    """Model work and nested deadlines, without relying on host scheduling."""
    def __init__(self):
        self.at = 0.0
        self.deadlines = []
        self.slices = []

    @contextmanager
    def limit(self, seconds):
        deadline = self.at + seconds
        if self.deadlines:
            deadline = min(deadline, self.deadlines[-1])
        self.deadlines.append(deadline)
        self.slices.append(seconds)
        try:
            yield
        finally:
            self.deadlines.pop()

    def work(self, seconds):
        end = self.at + seconds
        if self.deadlines and end > self.deadlines[-1]:
            self.at = self.deadlines[-1] + .000001
            raise ready.WaitBudgetExpired()
        self.at = end


class FairSliceTests(unittest.TestCase):
    setUp = fixtures.SchedulerTests.setUp

    def paths(self, count):
        scheduler.secure_directory(scheduler.root())
        directory = scheduler.root() / 'enrolled'
        scheduler.secure_directory(directory)
        paths = []
        for index in range(count):
            caller = str(uuid.UUID(int=index + 1)).upper()
            path = scheduler.enrollment_path(caller)
            ready.write_json(path, {})
            paths.append(path)
        return sorted(paths)

    def run_virtual(self, paths, durations, *, now=1000., posts=None):
        clock = VirtualBudget()
        visited = []
        completed = []
        def one(path, _now, _execution, **_kwargs):
            visited.append(path.name)
            clock.work(durations.get(path.name, .05))
            completed.append(path.name)
            return dict(enrollment=path.name, status='WAITING_REPLY',
                        file_posts=1 if posts is None else posts.get(path.name, 0),
                        next_due_epoch=now + 60)
        with patch.object(scheduler.time, 'monotonic', side_effect=lambda: clock.at), \
                patch.object(ready, 'runtime_limit', side_effect=clock.limit), \
                patch.object(scheduler, '_sweep_one', side_effect=one):
            result = scheduler.sweep(now=now)
        return result, visited, completed, clock

    def test_slow_first_does_not_use_fast_peers_budget(self):
        paths = self.paths(2)
        result, visited, completed, clock = self.run_virtual(
            paths, {paths[0].name: 9.})
        self.assertEqual(visited, [p.name for p in paths])
        self.assertEqual(completed, [paths[1].name])
        self.assertEqual(result['file_posts'], 1)
        self.assertEqual(result['status'], 'SWEPT_WITH_EPISODE_TIMEOUTS')
        self.assertTrue(result['pass_complete'])
        self.assertFalse(result['file_posts_complete'])
        self.assertLess(clock.at, scheduler.MAX_SWEEP_SECONDS)
        self.assertEqual(result['episodes'][0]['budget_seconds'], 4.)
        self.assertTrue(result['episodes'][0]['may_have_committed'])

    def test_eleven_fast_one_slow_complete_in_same_sweep(self):
        paths = self.paths(12)
        result, visited, completed, clock = self.run_virtual(
            paths, {paths[0].name: 9.})
        self.assertEqual(len(visited), 12)
        self.assertEqual(len(completed), 11)
        self.assertEqual(result['file_posts'], 11)
        self.assertEqual(result['episode_timeouts'], 1)
        self.assertLess(clock.at, 2.)

    def test_sixty_four_rows_progress_in_two_sweeps(self):
        paths = self.paths(64)
        durations = {p.name: .2 for p in paths}
        first, visited1, completed1, _ = self.run_virtual(paths, durations)
        second, visited2, completed2, _ = self.run_virtual(paths, durations, now=1060.)
        self.assertEqual(first['status'], 'SWEEP_BUDGET_EXHAUSTED')
        self.assertEqual(second['enrollment_order'][0], paths[len(visited1)].name)
        self.assertEqual(set(completed1 + completed2), {p.name for p in paths})
        self.assertLessEqual(len(visited1), 51)
        self.assertFalse(first['pass_complete'])
        self.assertEqual(second['input_operations'], 0)

    def test_future_due_is_ordering_only_and_stale_finished_ages_out(self):
        paths = self.paths(3)
        hints = {
            paths[0].name: dict(last_attempt_epoch=980., next_due_epoch=1050.),
            paths[1].name: dict(last_attempt_epoch=985., next_due_epoch=999.),
            paths[2].name: dict(last_attempt_epoch=981., status='FINISHED'),
        }
        previous = dict(at_epoch=990., last_enrollment='', service=dict(
            schema=scheduler.SERVICE_SCHEMA, enrollments=hints))
        ready.write_json(scheduler.root() / 'STATUS.json', previous)
        result, visited, _, _ = self.run_virtual(paths, {})
        self.assertEqual(visited, [paths[1].name, paths[0].name, paths[2].name])
        self.assertTrue(result['pass_complete'])
        order = scheduler._fair_order(paths, '', hints, 1201.)
        self.assertEqual(order[0], paths[0])
        self.assertEqual(scheduler._fair_order(paths, '', {
            paths[0].name: dict(last_attempt_epoch=2000., next_due_epoch=2050.,
                               status='FINISHED')}, 1000.)[0], paths[0])

    def test_clock_regression_discards_all_prior_service_hints(self):
        paths = self.paths(2)
        previous = dict(at_epoch=1200., last_enrollment=paths[0].name,
                        service=dict(schema=scheduler.SERVICE_SCHEMA, enrollments={
                            paths[1].name: dict(last_attempt_epoch=990., status='FINISHED')}))
        ready.write_json(scheduler.root() / 'STATUS.json', previous)
        result, visited, _, _ = self.run_virtual(paths, {})
        self.assertEqual(result['cadence']['status'], 'CLOCK_REGRESSED')
        self.assertEqual(visited[0], paths[1].name)
        self.assertEqual(len(visited), 2)

    def test_stale_finished_hint_competes_on_its_older_attempt(self):
        paths = self.paths(3)
        hints = {
            paths[0].name: dict(last_attempt_epoch=900., status='FINISHED'),
            paths[1].name: dict(last_attempt_epoch=950.),
            paths[2].name: dict(last_attempt_epoch=960.),
        }
        self.assertEqual(scheduler._fair_order(paths, '', hints, 1200.)[0],
                         paths[0])

    def test_recent_finished_hint_still_follows_unfinished_peers(self):
        paths = self.paths(3)
        hints = {
            path.name: dict(last_attempt_epoch=1150.) for path in paths
        }
        hints[paths[0].name]['status'] = 'FINISHED'
        self.assertEqual(scheduler._fair_order(paths, '', hints, 1200.)[-1],
                         paths[0])

    def test_malformed_hints_fall_back_to_cursor(self):
        paths = self.paths(2)
        for service in ('bad', dict(schema='bad', enrollments={}), dict(
                schema=scheduler.SERVICE_SCHEMA, enrollments=[]), dict(
                schema=scheduler.SERVICE_SCHEMA,
                enrollments={str(i): {} for i in range(257)})):
            with self.subTest(service=str(service)[:40]):
                previous = dict(at_epoch=999., service=service)
                self.assertEqual(scheduler._service_hints(previous, paths, 1000.), {})
        previous = dict(at_epoch=999., service=dict(schema=scheduler.SERVICE_SCHEMA,
            enrollments={paths[0].name: dict(last_attempt_epoch=True,
                last_completed_epoch=float('nan'), next_due_epoch=float('inf'),
                consecutive_timeouts=999)}))
        hints = scheduler._service_hints(previous, paths, 1000.)
        self.assertEqual(hints, {paths[0].name: {}})
        self.assertEqual(scheduler._fair_order(paths, paths[0].name, hints, 1000.)[0],
                         paths[1])
        self.assertFalse(scheduler._service_epoch(10 ** 400))
        previous['at_epoch'] = 10 ** 400
        self.assertEqual(scheduler._service_hints(previous, paths, 1000.), {})

    def test_repeated_timeout_increases_slice_finitely_without_retry(self):
        paths = self.paths(12)
        slow = paths[0].name
        slices = []
        for now in (1000., 1060., 1120., 1180.):
            result, visited, completed, _ = self.run_virtual(paths, {slow: 9.}, now=now)
            row = next(row for row in result['episodes'] if row['enrollment'] == slow)
            slices.append(row['budget_seconds'])
            self.assertEqual(visited.count(slow), 1)
            self.assertEqual(len(completed), 11)
        self.assertEqual(slices, [1., 2., 4., 4.])
        self.assertEqual(row['service_warning'], 'REPEATED_EPISODE_TIMEOUT')
        self.assertEqual(result['input_operations'], 0)

    def test_release_timeout_preserves_cursor_and_never_runs_rows(self):
        paths = self.paths(2)
        first, _, _, _ = self.run_virtual(paths, {})
        clock = VirtualBudget()
        def slow_release(*_args):
            clock.work(11.)
            return self.current
        with patch.object(scheduler.time, 'monotonic', side_effect=lambda: clock.at), \
                patch.object(ready, 'runtime_limit', side_effect=clock.limit), \
                patch.object(scheduler, '_sweep_one') as one:
            self.approved.side_effect = slow_release
            failed = scheduler.sweep(now=1060.)
        self.assertEqual(failed['phase'], 'VERIFY_RELEASE')
        self.assertEqual(failed['last_enrollment'], first['last_enrollment'])
        one.assert_not_called()

    def test_eight_second_release_leaves_finite_remaining_budget(self):
        paths = self.paths(2)
        clock = VirtualBudget()
        def release(*_args):
            clock.work(8.)
            return self.current
        def one(path, *_args, **_kwargs):
            clock.work(3.)
            return dict(enrollment=path.name, status='WAITING_REPLY', file_posts=1)
        with patch.object(scheduler.time, 'monotonic', side_effect=lambda: clock.at), \
                patch.object(ready, 'runtime_limit', side_effect=clock.limit), \
                patch.object(scheduler, '_sweep_one', side_effect=one):
            self.approved.side_effect = release
            failed = scheduler.sweep(now=1000.)
        self.assertEqual(failed['status'], 'SWEEP_BUDGET_EXHAUSTED')
        self.assertEqual(len(failed['episodes']), 1)
        self.assertLessEqual(failed['episodes'][0]['budget_seconds'], 2.)
        self.assertTrue(failed['episodes'][0]['may_have_committed'])
        self.assertFalse(failed['file_posts_complete'])

    def test_saved_service_hints_do_not_change_original_enrollment(self):
        paths = self.paths(2)
        before = {p: p.read_bytes() for p in paths}
        self.run_virtual(paths, {})
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.transport.assert_not_called()

    def test_real_timer_interrupts_slow_first_and_then_serves_fast_peer(self):
        paths = self.paths(2)
        visited = []
        def one(path, *_args, **_kwargs):
            visited.append(path.name)
            if path == paths[0]:
                time.sleep(.5)
            return dict(enrollment=path.name, status='WAITING_REPLY', file_posts=1)
        with patch.object(scheduler, 'MAX_SWEEP_SECONDS', .2), \
                patch.object(scheduler, 'MIN_EPISODE_SECONDS', .02), \
                patch.object(scheduler, 'MAX_EPISODE_SECONDS', .04), \
                patch.object(scheduler, '_sweep_one', side_effect=one):
            result = scheduler.sweep(now=1000.)
        self.assertEqual(visited, [p.name for p in paths])
        self.assertEqual(result['status'], 'SWEPT_WITH_EPISODE_TIMEOUTS')
        self.assertEqual(result['file_posts'], 1)
        self.assertFalse(result['file_posts_complete'])
        self.assertEqual(result['episodes'][1]['status'], 'WAITING_REPLY')
        self.transport.assert_not_called()

    def test_real_outer_timer_caps_many_slow_slices(self):
        # Keep enough real-time headroom for preparation and host scheduling.
        # Twelve fixed 60ms slices need 720ms even with no preparation, so the
        # 500ms outer alarm still interrupts an incomplete pass.
        paths = self.paths(12)
        visited = []
        def one(path, *_args, **_kwargs):
            visited.append(path.name)
            time.sleep(2.)
            return dict(enrollment=path.name, status='WAITING_REPLY', file_posts=1)
        with patch.object(scheduler, 'MAX_SWEEP_SECONDS', .5), \
                patch.object(scheduler, 'MIN_EPISODE_SECONDS', .06), \
                patch.object(scheduler, 'MAX_EPISODE_SECONDS', .06), \
                patch.object(scheduler, '_sweep_one', side_effect=one):
            result = scheduler.sweep(now=1000.)
        self.assertEqual(result['status'], 'SWEEP_BUDGET_EXHAUSTED')
        self.assertFalse(result['pass_complete'])
        self.assertGreater(len(visited), 1)
        self.assertLess(len(visited), len(paths))
        self.assertTrue(result['episode_timeouts'])
        self.assertEqual(result['file_posts'], 0)
        self.assertFalse(result['file_posts_complete'])
        self.transport.assert_not_called()

    def test_real_outer_alarm_interrupts_work_without_inner_alarms(self):
        # A loop deadline alone cannot interrupt one blocking row. Disable
        # only nested alarms to distinguish the real outer alarm explicitly.
        paths = self.paths(2)
        visited = []
        completed = []
        limit = ready.runtime_limit
        calls = []
        @contextmanager
        def outer_only(seconds):
            calls.append(seconds)
            if len(calls) == 1 and seconds > .1:
                with limit(seconds):
                    yield
            else:
                yield
        def one(path, *_args, **_kwargs):
            visited.append(path.name)
            time.sleep(2.)
            completed.append(path.name)
            return dict(enrollment=path.name, status='WAITING_REPLY', file_posts=1)
        with patch.object(scheduler, 'MAX_SWEEP_SECONDS', .3), \
                patch.object(scheduler, 'MIN_EPISODE_SECONDS', .03), \
                patch.object(scheduler, 'MAX_EPISODE_SECONDS', .03), \
                patch.object(ready, 'runtime_limit', side_effect=outer_only), \
                patch.object(scheduler, '_sweep_one', side_effect=one):
            result = scheduler.sweep(now=1000.)
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreater(calls[0], .1)
        self.assertEqual(visited, [paths[0].name])
        self.assertEqual(completed, [])
        self.assertEqual(result['status'], 'SWEEP_BUDGET_EXHAUSTED')
        self.assertFalse(result['pass_complete'])
        self.assertEqual(result['episode_timeouts'], 1)
        self.assertEqual(result['file_posts'], 0)
        self.assertFalse(result['file_posts_complete'])
        self.transport.assert_not_called()

    def test_real_commit_timeout_preserves_original_and_other_peer_progress(self):
        callers = [str(uuid.UUID(int=i + 1)).upper() for i in range(2)]
        for caller in callers:
            fixtures.SchedulerTests.enroll(self, caller)
        slow = min(callers, key=lambda caller: scheduler.enrollment_path(caller).name)
        tick = reask.tick_locked
        def interrupted(caller, *args, **kwargs):
            state = tick(caller, *args, **kwargs)
            if caller == slow:
                raise ready.WaitBudgetExpired()
            return state
        with patch.object(reask, 'tick_locked', side_effect=interrupted):
            result = scheduler.sweep(now=1001.)
        self.assertEqual(result['file_posts'], 1)
        self.assertEqual(result['episode_timeouts'], 1)
        self.assertFalse(result['file_posts_complete'])
        self.assertEqual([reask.load(c)['file_posts'] for c in callers], [1, 1])
        # A fresh scheduled pass observes the original committed due time.
        # It never replays the timed-out inquiry to repair incomplete telemetry.
        later = scheduler.sweep(now=1002.)
        self.assertEqual(later['file_posts'], 0)
        self.assertEqual([reask.load(c)['file_posts'] for c in callers], [1, 1])
        self.transport.assert_not_called()

    begin = fixtures.SchedulerTests.begin
    live = fixtures.SchedulerTests.live


if __name__ == '__main__':
    unittest.main()
