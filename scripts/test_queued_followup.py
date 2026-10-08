"""Measured follow-up queue shape; offline, no terminal input."""
import unittest
from unittest.mock import patch
import cmux_bridge as b


class QueuedFollowupTests(unittest.TestCase):
    marker = 'nonce-queue-example'

    def screen(self, payload):
        return ('• Working (3m 54s • esc to interrupt)\n'
                '• Queued follow-up inputs\n  ↳ ' + payload + '\n'
                '    shift+← edit last queued message\n'
                '› Ask Codex to do anything\nGPT-6-Astra xhigh\n')

    def test_measured_header_classifies_queue_not_compose(self):
        screen = self.screen('DONE|task|' + self.marker)
        self.assertEqual(b.classify_submission_failure(screen, self.marker, True),
                         b.DELIVERY_QUEUED_AT_RECEIVER)

    def test_wrapped_marker(self):
        self.assertTrue(b.pending_queue_holds(self.screen('nonce-queue-\n  example'), self.marker))

    def test_other_queued_message_is_not_ours(self):
        self.assertFalse(b.pending_queue_holds(self.screen('other-nonce'), self.marker))

    def test_transcript_marker_above_queue_is_not_ours(self):
        self.assertFalse(b.pending_queue_holds(self.marker + '\n' + self.screen('other'), self.marker))

    def test_compose_marker_below_queue_is_not_ours(self):
        screen = self.screen('other').replace('Ask Codex to do anything', self.marker)
        self.assertFalse(b.pending_queue_holds(screen, self.marker))

    def test_empty_marker_is_not_queue_evidence(self):
        self.assertFalse(b.pending_queue_holds(self.screen('other'), ''))

    def test_send_path_never_confirms_or_resends_queue(self):
        text = 'STATUS: ' + self.marker
        with patch.object(b, 'read_screen', side_effect=['› \nGPT-6 high', self.screen(text)]), \
                patch.object(b, 'send_text') as send, patch.object(b, 'send_key') as key, \
                patch.object(b.time, 'sleep'):
            with self.assertRaises(b.DispatchUnconfirmed) as caught:
                b._submit_text_once('peer', text, marker=self.marker)
            self.assertEqual(caught.exception.state, b.DELIVERY_QUEUED_AT_RECEIVER)
            send.assert_called_once()
            key.assert_called_once_with('peer', 'enter')
