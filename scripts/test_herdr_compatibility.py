"""Keep real legacy discovery and new guidance behavior separate."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import cmux_native_delivery as native


class HerdrCompatibility(unittest.TestCase):
    def test_outer_herdr_terminal_is_not_a_native_recipient(self):
        row=dict(tty='ttys999',target_surface_uuid='surface-a',workspace_uuid='workspace-a')
        listing=subprocess.CompletedProcess([],0,stdout=f'123 {os.getuid()} ttys999 /opt/homebrew/bin/herdr\n')
        with patch.object(native,'_process_observation',return_value=listing), \
                patch.object(native.identity,'process') as process:
            with self.assertRaisesRegex(native.NativeDeliveryError,'NATIVE_TARGET_AGENT_ABSENT'):
                native._target_process(row)
            process.assert_not_called()

    def test_hook_is_context_only_and_observes_event(self):
        hook=Path(__file__).with_name('collaboration_context_hook.py')
        for event in ('PreToolUse','PostToolUse','Stop'):
            r=subprocess.run([sys.executable,'-B',str(hook)],
                input=json.dumps(dict(hook_event_name=event)),text=True,capture_output=True,check=True)
            self.assertEqual(r.stdout,'')
        for herdr in ('0','1'):
            r=subprocess.run([sys.executable,'-B',str(hook)],env=dict(os.environ,HERDR_ENV=herdr),
                input=json.dumps(dict(hook_event_name='SessionStart')),text=True,capture_output=True,check=True)
            output=json.loads(r.stdout)
            self.assertEqual(set(output),{'hookSpecificOutput'})
            context=output['hookSpecificOutput']['additionalContext']
            self.assertIn('retain its registered controller',context)
            self.assertEqual('skills/herdr-collaboration/SKILL.md' in context,herdr=='1')


if __name__=='__main__':unittest.main()
