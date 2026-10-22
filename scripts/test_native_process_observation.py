"""L463-1 controls: bounded read-only observation never weakens target identity."""
import copy
import os
import subprocess
import unittest
from unittest.mock import patch

import cmux_native_delivery as native
import cmux_identity_budget as budget
import test_claude_native_versioned_identity as versioned
import test_native_independent as independent
import test_successor_native_delivery as successor_tests
import successor_native_delivery as successor


class ProcessObservationTests(unittest.TestCase):
    def setUp(self):
        versioned.VersionedClaudeTests.setUp(self)
        self.clock = 100.0
        self.calls = []
        self.timeouts = []
        self.reads = []
        self.listing = str(self.process['pid'])+' '+str(os.getuid())+' '+self.row['tty']+' '+self.process['executable']+'\n'

    def observe(self, *, failures=0, duration=0.1, tty=None, inventory=None, drift=False):
        def ps(argv, **kwargs):
            self.calls.append(list(argv)); self.timeouts.append(kwargs['timeout'])
            if len(self.calls) <= failures:
                self.clock += kwargs['timeout']
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'],
                    output=b'9999 /untrusted/partial/codex\n')
            self.clock += duration
            output = (self.listing if inventory is None else inventory) if '-t' in argv else (tty or self.row['tty'])
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr='')
        def process(pid, **kwargs):
            self.reads.append(pid)
            result=copy.deepcopy(self.process)
            if drift and len(self.reads)>1:result['birth'][1]+=1
            return result
        with patch.object(budget.time,'monotonic',side_effect=lambda:self.clock), \
             patch.object(native.subprocess,'run',side_effect=ps), \
             patch.object(native.identity,'process',side_effect=process):
            return native._target_process(self.row)

    def test_timeout_then_success_repeats_only_exact_tty_inventory(self):
        result=self.observe(failures=1)
        self.assertEqual(result,self.process)
        expected=['/bin/ps','-ww','-t',self.row['tty'],'-o','pid=,uid=,tty=,comm=']
        self.assertEqual(self.calls[:2],[expected,expected])
        self.assertNotIn('-U',self.calls[0])
        self.assertEqual(self.reads,[1234,1234])
        self.assertLess(self.clock-100.,10.)
        self.assertLess(self.timeouts[2],5.)

    def test_exhausted_timeout_never_uses_partial_inventory(self):
        with self.assertRaisesRegex(native.NativeDeliveryError,'OBSERVATION_TIMEOUT') as error:
            self.observe(failures=2)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.reads,[])
        self.assertEqual(error.exception.details['input_operations'],0)
        self.assertFalse(error.exception.details['partial_output_used'])
        self.assertEqual(self.clock,110.)

    def test_outer_shorter_budget_is_not_extended(self):
        with patch.object(budget.time,'monotonic',side_effect=lambda:self.clock), \
             self.assertRaisesRegex(native.NativeDeliveryError,'OBSERVATION_TIMEOUT'), budget.limit(2.):
            self.observe(failures=2)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.timeouts,[2.])
        self.assertEqual(self.reads,[])

    def test_tty_check_timeout_uses_remaining_whole_observation_budget(self):
        count=0
        def ps(argv, **kwargs):
            nonlocal count
            count+=1;self.calls.append(argv);self.timeouts.append(kwargs['timeout'])
            if '-t' in argv:
                self.clock+=4.
                return subprocess.CompletedProcess(argv,0,stdout=self.listing)
            self.clock+=kwargs['timeout']
            raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
        with patch.object(budget.time,'monotonic',side_effect=lambda:self.clock), \
             patch.object(native.subprocess,'run',side_effect=ps), \
             patch.object(native.identity,'process',return_value=self.process):
            with self.assertRaisesRegex(native.NativeDeliveryError,'OBSERVATION_TIMEOUT'):
                native._target_process(self.row)
        self.assertEqual(count,3)
        self.assertEqual(self.timeouts,[5.,5.,1.])
        self.assertEqual(self.clock,110.)

    def test_invalid_tty_selectors_never_launch_a_command(self):
        for value in ['',None,'-A','ttys041,ttys042','ttys041\n','../ttys041','tty?']:
            self.row['tty']=value
            with patch.object(native.subprocess,'run') as ps:
                with self.assertRaisesRegex(native.NativeDeliveryError,'TTY_SELECTOR_INVALID'):
                    native._target_process(self.row)
            ps.assert_not_called()

    def test_duplicate_pids_are_refused_not_deduplicated_to_one_agent(self):
        with self.assertRaisesRegex(native.NativeDeliveryError,'INVENTORY_INVALID'):
            self.observe(inventory=self.listing+self.listing)

    def test_distinct_native_processes_on_same_tty_are_not_unique(self):
        listing=self.listing+self.listing.replace('1234 ', '5678 ', 1)
        def process(pid, **kwargs):
            value=copy.deepcopy(self.process);value['pid']=pid
            return value
        def ps(argv, **kwargs):
            return subprocess.CompletedProcess(argv,0,stdout=listing if '-t' in argv else self.row['tty'])
        with patch.object(native.identity,'process',side_effect=process), \
             patch.object(native.subprocess,'run',side_effect=ps):
            with self.assertRaisesRegex(native.NativeDeliveryError,'PROCESS_NOT_UNIQUE'):
                native._target_process(self.row)

    def test_ordinary_rebind_retries_only_tty_observation(self):
        with independent.NativeCase() as case:
            binding=case.bind();original_ps=native.subprocess.run
            calls=[]
            def ps(argv, **kwargs):
                calls.append(argv)
                if len(calls)==1:raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
                return original_ps(argv,**kwargs)
            with patch.object(native.subprocess,'run',side_effect=ps):
                native.require_bound(case.bridge,independent.TARGET,binding,independent.PAYLOAD)
            self.assertEqual(calls,[['/bin/ps','-p',str(independent.PID),'-o','tty=']]*2)
            self.assertEqual(case.input_operations,[])

    def test_successor_rebind_retries_only_tty_observation(self):
        case=successor_tests.SuccessorNativeDeliveryTests('test_actual_journal_precedes_paste_and_key_and_repeated_submit_has_zero_input')
        case.setUp()
        try:
            binding=successor.bind_target(case.bridge,case.artifact,case.text)
            original_ps=native.subprocess.run;calls=[]
            def ps(argv, **kwargs):
                calls.append(argv)
                if len(calls)==1:raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
                return original_ps(argv,**kwargs)
            with patch.object(native.subprocess,'run',side_effect=ps):
                successor.require_bound(case.bridge,case.artifact,binding,case.text)
            self.assertEqual(calls,[['/bin/ps','-p',str(case.process['pid']),'-o','tty=']]*2)
            self.assertEqual(case.bridge.operations,[])
        finally:
            case.doCleanups()

    def test_foreign_uid_or_inaccessible_candidate_does_not_get_retried(self):
        with patch.object(native.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout=self.listing)) as ps, \
             patch.object(native.identity,'process',side_effect=native.identity.IdentityError('foreign process')):
            with self.assertRaises(native.identity.IdentityError):native._target_process(self.row)
        self.assertEqual(ps.call_count,1)

    def test_foreign_uid_rows_are_skipped_before_kernel_argument_access(self):
        foreign='9999 '+str(os.getuid()+1)+' '+self.row['tty']+' /bin/codex\n'
        self.assertEqual(self.observe(inventory=foreign+self.listing),self.process)
        self.assertEqual(self.reads,[1234,1234])

    def test_tty_column_mismatch_is_refused_before_selecting_native_client(self):
        listing=self.listing.replace(self.row['tty'],'ttys000')
        with self.assertRaisesRegex(native.NativeDeliveryError,'INVENTORY_INVALID'):
            self.observe(inventory=listing)
        self.assertEqual(self.reads,[])

    def test_bare_login_shell_label_keeps_dead_target_diagnostic(self):
        self.process['executable']='/bin/zsh'
        shell='1234 '+str(os.getuid())+' '+self.row['tty']+' -zsh\n'
        with self.assertRaisesRegex(native.NativeDeliveryError,'RETURNED_TO_SHELL') as error:
            self.observe(inventory=shell)
        self.assertEqual(error.exception.details['native_agent_count'],0)

    def test_identity_changes_after_timeout_still_refuse(self):
        with self.assertRaisesRegex(native.NativeDeliveryError,'PROCESS_CHANGED'):
            self.observe(failures=1,drift=True)

    def test_wrong_workspace_after_timeout_still_refuse(self):
        self.process['env']['CMUX_WORKSPACE_ID']='OTHER'
        with self.assertRaisesRegex(native.NativeDeliveryError,'WORKSPACE_MISMATCH'):
            self.observe(failures=1)

    def test_wrong_tty_after_timeout_still_refuse(self):
        with self.assertRaisesRegex(native.NativeDeliveryError,'TTY_MISMATCH'):
            self.observe(failures=1,tty='ttys999')

    def test_non_timeout_subprocess_error_never_retries(self):
        with patch.object(native.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['/bin/ps'])) as ps:
            with self.assertRaises(subprocess.CalledProcessError):native._target_process(self.row)
        self.assertEqual(ps.call_count,1)


if __name__=='__main__':unittest.main()
