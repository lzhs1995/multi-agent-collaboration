import copy
import tempfile
import unittest
from pathlib import Path

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


if __name__=='__main__':unittest.main()
