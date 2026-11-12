import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import collaboration_profile as p


class Profiles(unittest.TestCase):
    def setUp(self):
        self.endpoint = {'transport': 'herdr', 'socket': '/tmp/test-server-a/herdr.sock'}
        self.target = dict(endpoint=self.endpoint, pane_id='w1:p2',
                           terminal_id='terminal-a', native_session_id='original-session')
        self.row = dict(pane_id='w1:p2', terminal_id='terminal-a', agent_status='done',
                        agent_session=dict(kind='id', value='original-session'))
        self.live = {'result': {'agents': [self.row]}}

    def test_legacy_inside_herdr_is_not_migrated(self):
        old = {'controller': {'source': '/original/release'}, 'participants': []}
        before = copy.deepcopy(old)
        self.assertEqual(p.select_profile(old, {'HERDR_ENV':'1'})['profile'], 'original')
        self.assertEqual(old, before)

    def test_registered_cmux_wins_over_environment(self):
        r = {'schema':'collaboration-profile-v1','task_id':'t','profile':'cmux'}
        self.assertEqual(p.select_profile(r, {'HERDR_ENV':'1'})['profile'], 'cmux')

    def test_new_default_does_not_enroll(self):
        self.assertFalse(p.select_profile(None, {'HERDR_ENV':'1'})['enrolled'])
        self.assertEqual(p.select_profile(None, {})['profile'], 'cmux')

    def test_receiver_overrides_sender_environment_for_new_task_only(self):
        outside = p.select_profile(None, {}, target=self.target)
        self.assertEqual(outside['profile'], 'herdr')
        inside = p.select_profile(None, {'HERDR_ENV': '1'}, requested_profile='cmux')
        self.assertEqual(inside['profile'], 'cmux')
        for result in (outside, inside):
            self.assertFalse(result['enrolled'])
            self.assertFalse(result['migrate'])
            self.assertFalse(result['identity_verified'])

    def test_receiver_cannot_migrate_legacy_registration(self):
        old = {'controller': {'source': '/original/release'}, 'participants': []}
        before = copy.deepcopy(old)
        result = p.select_profile(old, {}, target=self.target)
        self.assertEqual(result['profile'], 'original')
        self.assertEqual(result['controller'], old['controller'])
        self.assertEqual(result['route_note'], 'USE_ORIGINAL_CONTROLLER_NOT_RECEIVER_OVERRIDE')
        self.assertEqual(old, before)

    def test_registered_profile_conflict_keeps_registration_and_reports_failure(self):
        registered = {'schema': 'collaboration-profile-v1', 'task_id': 'existing', 'profile': 'cmux'}
        before = copy.deepcopy(registered)
        result = p.select_profile(registered, {}, target=self.target)
        self.assertEqual(result['profile'], 'cmux')
        self.assertEqual(result['conflict'], 'REGISTERED_PROFILE_DIFFERS_FROM_RECEIVER')
        self.assertEqual(registered, before)
        with tempfile.TemporaryDirectory() as tmp:
            registration, target = Path(tmp)/'registration.json', Path(tmp)/'target.json'
            registration.write_text(json.dumps(registered))
            target.write_text(json.dumps(self.target))
            output = io.StringIO()
            with patch('sys.stdout', output):
                rc = p.main(['route', '--registration', str(registration), '--target', str(target)])
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(output.getvalue())['profile'], 'cmux')
            self.assertEqual(json.loads(registration.read_text()), before)

    def test_matching_registration_has_no_conflict(self):
        registered = {'schema': 'collaboration-profile-v1', 'task_id': 't', 'profile': 'herdr'}
        self.assertNotIn('conflict', p.select_profile(registered, {}, target=self.target))

    def test_receiver_arguments_cannot_disagree(self):
        with self.assertRaisesRegex(p.ProfileError, 'RECEIVER_PROFILE_CONFLICT'):
            p.select_profile(None, {}, target=self.target, requested_profile='cmux')

    def test_invalid_receiver_is_not_treated_as_environment_fallback(self):
        for target in ([], {}, {'endpoint': {'transport': 'unknown'}}):
            with self.subTest(target=target):
                with self.assertRaisesRegex(p.ProfileError, 'INVALID_RECEIVER_ENDPOINT'):
                    p.select_profile(None, {'HERDR_ENV': '1'}, target=target)

    def test_explicit_nonobject_json_files_never_fall_back_to_environment(self):
        script = Path(p.__file__)
        for argument, expected in (('--registration', 'INVALID_TASK_REGISTRATION'),
                                   ('--target', 'INVALID_RECEIVER_ENDPOINT')):
            for value in (None, [], 'not-an-object', False):
                with self.subTest(argument=argument, value=value), tempfile.TemporaryDirectory() as tmp:
                    document = Path(tmp) / 'input.json'
                    original = json.dumps(value)
                    document.write_text(original)
                    result = subprocess.run(
                        [sys.executable, '-B', str(script), 'route', argument, str(document)],
                        env=dict(os.environ, HERDR_ENV='1'), text=True,
                        capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(json.loads(result.stdout),
                                     {'error': expected, 'input_sent': False})
                    self.assertEqual(document.read_text(), original)
                    self.assertEqual(result.stderr, '')

    def test_same_pane_on_different_servers_is_refused(self):
        with self.assertRaisesRegex(p.ProfileError, 'SERVER_CHANGED'):
            p.verify_target(self.target, self.live, dict(self.endpoint, socket='/tmp/server-b.sock'))

    def test_two_inner_sessions_sharing_outer_surface_remain_distinct(self):
        other = dict(self.target, pane_id='w1:p4', terminal_id='terminal-b',
                     native_session_id='other-session', outer_surface='same')
        self.target['outer_surface'] = 'same'
        self.assertNotEqual(p.target_key(self.target), p.target_key(other))

    def test_replaced_session_is_refused(self):
        self.row['agent_session']['value'] = 'replacement'
        with self.assertRaisesRegex(p.ProfileError, 'NATIVE_SESSION_CHANGED'):
            p.verify_target(self.target,self.live,self.endpoint)

    def test_replaced_terminal_is_refused(self):
        self.row['terminal_id'] = 'replacement'
        with self.assertRaisesRegex(p.ProfileError, 'TERMINAL_CHANGED'):
            p.verify_target(self.target,self.live,self.endpoint)

    def test_lifecycle_never_proves_this_task_complete(self):
        for state in ('working','idle','done','blocked','unknown'):
            self.row['agent_status'] = state
            result = p.verify_target(self.target,self.live,self.endpoint)
            self.assertFalse(result['task_completed'])
            self.assertFalse(result['atomic_send_guard'])

    def test_timeout_after_submission_never_authorizes_resend(self):
        for status in ('SUBMITTED','timeout','agent_prompt_stalled',None):
            self.assertFalse(p.observation(submission=status)['resend_authorized'])

    def test_file_integrity_does_not_accept_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report';path.write_text('report')
            digest=p.artifact_matches(path,'wrong')['sha256']
            result=p.artifact_matches(path,digest)
            self.assertTrue(result['integrity_matches'])
            self.assertFalse(result['accepted'])

    def test_ambiguous_live_response_is_refused(self):
        self.live['result']['agents'].append(dict(self.row))
        with self.assertRaisesRegex(p.ProfileError,'TARGET_NOT_UNIQUE'):
            p.verify_target(self.target,self.live,self.endpoint)

    def test_malformed_live_response_refused_without_traceback(self):
        for response in ([], {'result': None}, {'result': {'agents': [None]}}):
            with self.subTest(response=response):
                with self.assertRaisesRegex(p.ProfileError, 'INVALID_LIVE_RESPONSE'):
                    p.verify_target(self.target, response, self.endpoint)


class LocalServerChecks(unittest.TestCase):
    def setUp(self):
        Profiles.setUp(self)
        self.status = {'server': {'running': True, 'status': 'running',
                                 'compatible': True, 'endpoint_compatible': True,
                                 'socket': self.endpoint['socket'], 'session': 'server-a'}}

    def runner(self, status=None, live=None):
        return Mock(side_effect=[subprocess.CompletedProcess([], 0, json.dumps(value))
                                 for value in (self.status if status is None else status,
                                               self.live if live is None else live)])

    def test_outer_caller_uses_named_server_for_every_read_without_spoofing_env(self):
        env = {'PATH': '/bin', 'CMUX_SURFACE_ID': 'shared-outer',
               'HERDR_SOCKET_PATH': '/unrelated/inherited.sock'}
        before = dict(env)
        run = self.runner()
        result = p.check_herdr(self.target, session='server-a', environ=env, runner=run)
        self.assertTrue(result['identity_matches'])
        self.assertFalse(result['input_sent'])
        self.assertFalse(result['atomic_send_guard'])
        self.assertEqual(result['selector'], {'kind': 'session', 'name': 'server-a'})
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ['herdr', '--session', 'server-a', 'status', '--json'],
            ['herdr', '--session', 'server-a', 'agent', 'list']])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['env'], before)
            self.assertEqual(call.kwargs['timeout'], 10)
            self.assertTrue(call.kwargs['check'])
        self.assertEqual(env, before)

    def test_inherited_selection_uses_env_but_also_checks_actual_status_socket(self):
        run = self.runner()
        env = {'HERDR_ENV': '1', 'HERDR_SOCKET_PATH': self.endpoint['socket']}
        result = p.check_herdr(self.target, environ=env, runner=run)
        self.assertEqual(result['selector']['kind'], 'inherited')
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ['herdr', 'status', '--json'], ['herdr', 'agent', 'list']])
        self.status['server']['socket'] = '/another/server.sock'
        run = self.runner()
        with self.assertRaisesRegex(p.ProfileError, 'SERVER_CHANGED'):
            p.check_herdr(self.target, environ=env, runner=run)
        self.assertEqual(run.call_count, 1)

    def test_two_servers_with_same_pane_are_not_interchangeable(self):
        server_b = copy.deepcopy(self.status)
        server_b['server'].update(session='server-b', socket='/tmp/server-b/herdr.sock')
        run = self.runner(status=server_b)
        with self.assertRaisesRegex(p.ProfileError, 'SERVER_CHANGED'):
            p.check_herdr(self.target, session='server-b', environ={}, runner=run)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1:3], ['--session', 'server-b'])

    def test_actual_server_session_must_match_selector(self):
        run = self.runner()
        with self.assertRaisesRegex(p.ProfileError, 'SESSION_SELECTOR_CHANGED'):
            p.check_herdr(self.target, session='server-b', environ={}, runner=run)
        self.assertEqual(run.call_count, 1)

    def test_changed_occupant_is_rejected_after_same_server_check(self):
        for field, value, expected in (
                ('terminal_id', 'replacement-terminal', 'TERMINAL_CHANGED'),
                ('agent_session', {'kind': 'id', 'value': 'new-session'}, 'NATIVE_SESSION_CHANGED')):
            live = copy.deepcopy(self.live)
            live['result']['agents'][0][field] = value
            run = self.runner(live=live)
            with self.subTest(field=field):
                with self.assertRaisesRegex(p.ProfileError, expected):
                    p.check_herdr(self.target, session='server-a', environ={}, runner=run)
                self.assertEqual(run.call_count, 2)

    def test_missing_selection_refused_before_any_command(self):
        run = Mock()
        with self.assertRaisesRegex(p.ProfileError, 'HERDR_ENV_OR_EXPLICIT_SESSION_REQUIRED'):
            p.check_herdr(self.target, environ={}, runner=run)
        run.assert_not_called()

    def test_wrong_inherited_socket_refused_before_any_command(self):
        run = Mock()
        with self.assertRaisesRegex(p.ProfileError, 'SELECT_PINNED_SERVER_FIRST'):
            p.check_herdr(self.target, environ={'HERDR_ENV': '1',
                          'HERDR_SOCKET_PATH': '/wrong/server.sock'}, runner=run)
        run.assert_not_called()

    def test_stopped_or_incompatible_service_never_starts_or_falls_back(self):
        for field, value, expected in (
                ('running', False, 'HERDR_SERVER_NOT_RUNNING'),
                ('status', 'stopped', 'HERDR_SERVER_NOT_RUNNING'),
                ('compatible', False, 'HERDR_SERVER_INCOMPATIBLE'),
                ('endpoint_compatible', False, 'HERDR_SERVER_INCOMPATIBLE')):
            status = copy.deepcopy(self.status)
            status['server'][field] = value
            run = self.runner(status=status)
            with self.subTest(field=field):
                with self.assertRaisesRegex(p.ProfileError, expected):
                    p.check_herdr(self.target, session='server-a', environ={}, runner=run)
                self.assertEqual(run.call_count, 1)

    def test_command_failure_and_timeout_never_retry_or_change_selector(self):
        for exc in (subprocess.TimeoutExpired(['herdr'], 10),
                    subprocess.CalledProcessError(1, ['herdr'])):
            for after_status in (False, True):
                steps = ([subprocess.CompletedProcess([], 0, json.dumps(self.status))]
                         if after_status else []) + [exc]
                run = Mock(side_effect=steps)
                with self.subTest(error=type(exc).__name__, after_status=after_status):
                    with self.assertRaises(type(exc)):
                        p.check_herdr(self.target, session='server-a', environ={}, runner=run)
                    self.assertEqual(run.call_count, len(steps))
                    for call in run.call_args_list:
                        self.assertEqual(call.args[0][:3], ['herdr', '--session', 'server-a'])

    def test_malformed_status_and_agent_response_fail_closed(self):
        for stdout in ('not-json', 'null', '[]'):
            run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout))
            with self.subTest(stdout=stdout):
                with self.assertRaises(ValueError):
                    p.check_herdr(self.target, session='server-a', environ={}, runner=run)
                self.assertEqual(run.call_count, 1)

    def test_supported_import_and_direct_cli_do_not_write_source_bytecode(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'collaboration_profile.py'
            source.write_bytes(Path(p.__file__).read_bytes())
            env = dict(os.environ)
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            env.pop('PYTHONPYCACHEPREFIX', None)
            for args in (['-BE', '-c', 'import collaboration_profile'],
                         ['-E', str(source), 'route']):
                with self.subTest(args=args):
                    run = subprocess.run([sys.executable] + args, cwd=tmp, env=env,
                                         text=True, capture_output=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual(sorted(x.name for x in Path(tmp).iterdir()),
                                     ['collaboration_profile.py'])

    def test_module_disables_bytecode_for_later_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'collaboration_profile.py'
            source.write_bytes(Path(p.__file__).read_bytes())
            (Path(tmp)/'next_module.py').write_text('value = 1\n')
            env = dict(os.environ)
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            env.pop('PYTHONPYCACHEPREFIX', None)
            code = ('import importlib.util; '
                    's=importlib.util.spec_from_file_location("profile_test", "collaboration_profile.py"); '
                    'm=importlib.util.module_from_spec(s); '
                    'exec(compile(open("collaboration_profile.py").read(), "collaboration_profile.py", "exec"),m.__dict__); '
                    'import next_module')
            run = subprocess.run([sys.executable, '-E', '-c', code], cwd=tmp, env=env,
                                 text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse((Path(tmp)/'__pycache__').exists())


if __name__=='__main__':unittest.main()
