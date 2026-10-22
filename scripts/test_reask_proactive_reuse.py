"""Explicit proactive authority cannot silently disappear on episode reuse."""
import copy
from pathlib import Path
import unittest

import executor_reask as reask
import test_executor_reask_scheduler as fixtures
from test_executor_reask_scheduler import CALLER,SUP,WORKSPACE


class ProactiveReuseTests(unittest.TestCase):
    def setUp(self):
        fixtures.SchedulerTests.setUp(self)
        self.auth=self.home/'authority.txt'
        self.auth.write_text('Continue original task inquiries and proactive hooks.')

    def begin(self, *, flag=False, include=True):
        value=dict(enabled=True,owner=SUP,overall_task='original-work',
            authorization_file=str(self.auth),reservation_inquiries=True)
        if include:value['proactive_hook']=flag
        return reask.start(CALLER,SUP,'original-task',workspace=WORKSPACE,
            now=1000.,continuing=True,continuation=value,file_only=True)

    def test_missing_legacy_flag_refuses_explicit_enable_without_mutation(self):
        original=self.begin(include=False);before=reask.state_path(CALLER).read_bytes()
        with self.assertRaisesRegex(ValueError,'changing proactive hook authorization'):
            self.begin(flag=True)
        self.assertEqual(reask.state_path(CALLER).read_bytes(),before)
        self.assertEqual(reask.load(CALLER)['episode_id'],original['episode_id'])
        self.assertNotIn('proactive_hook',reask.load(CALLER)['continuation'])

    def test_enabled_flag_cannot_be_silently_disabled(self):
        original=self.begin(flag=True);before=reask.state_path(CALLER).read_bytes()
        with self.assertRaisesRegex(ValueError,'changing proactive hook authorization'):
            self.begin(flag=False)
        self.assertEqual(reask.state_path(CALLER).read_bytes(),before)
        self.assertTrue(reask.load(CALLER)['continuation']['proactive_hook'])
        self.assertEqual(reask.load(CALLER)['episode_id'],original['episode_id'])

    def test_same_explicit_true_reuses_exact_original_bytes(self):
        original=self.begin(flag=True);before=reask.state_path(CALLER).read_bytes()
        self.assertEqual(self.begin(flag=True),original)
        self.assertEqual(reask.state_path(CALLER).read_bytes(),before)

    def test_omitted_legacy_false_stays_absent(self):
        original=self.begin(include=False);before=reask.state_path(CALLER).read_bytes()
        self.assertEqual(self.begin(flag=False),original)
        self.assertEqual(self.begin(include=False),original)
        self.assertEqual(reask.state_path(CALLER).read_bytes(),before)

    def test_malformed_boolean_refused_before_reuse(self):
        self.begin(include=False);before=reask.state_path(CALLER).read_bytes()
        for value in (1,0,'true',None,[],{}):
            with self.assertRaisesRegex(ValueError,'proactive hook requires explicit'):
                self.begin(flag=value)
            self.assertEqual(reask.state_path(CALLER).read_bytes(),before)

    def test_malformed_original_flag_is_not_false_by_coercion(self):
        original=self.begin(include=False);original['continuation']['proactive_hook']=0
        reask.save(original);before=reask.state_path(CALLER).read_bytes()
        with self.assertRaisesRegex(ValueError,'changing proactive hook authorization'):
            self.begin(flag=False)
        self.assertEqual(reask.state_path(CALLER).read_bytes(),before)

    def test_new_explicit_authority_requires_continuation_and_reservation(self):
        value=dict(enabled=True,owner=SUP,overall_task='original-work',
            authorization_file=str(self.auth),proactive_hook=True)
        with self.assertRaisesRegex(ValueError,'proactive hook requires explicit'):
            reask.start(CALLER,SUP,'original-task',workspace=WORKSPACE,now=1000.,
                continuing=True,continuation=value)
        self.assertFalse(reask.state_path(CALLER).exists())


if __name__=='__main__':unittest.main()
