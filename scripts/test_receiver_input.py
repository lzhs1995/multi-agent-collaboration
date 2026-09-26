"""No live terminal writes: regression probes use the public send entrypoint."""
import unittest
from unittest.mock import patch
import cmux_bridge as bridge


class ReceiverInputTests(unittest.TestCase):
    def test_shell_or_unknown_does_not_receive_any_bytes_or_keys(self):
        screens = ["researcher@mac ~ %", "bash-3.2$ ", "PS C:\\work> ",
                   "› Ask Codex to do anything\nGPT-6 high\nresearcher@mac ~ %",
                   "❯ ", "", "ordinary text"]
        for screen in screens:
            for force in (False, True):
                with self.subTest(screen=screen, force=force), patch.object(bridge, "read_screen", return_value=screen), \
                     patch.object(bridge, "send_text") as send, patch.object(bridge, "send_key") as key:
                    with self.assertRaises(bridge.DispatchUnconfirmed) as error:
                        bridge.submit_text("peer", "STATUS: continuation", marker="marker", force_compose=force)
                    self.assertEqual(error.exception.state, bridge.SUPERVISOR_DID_NOT_SUBMIT)
                    send.assert_not_called()
                    key.assert_not_called()

    def test_no_marker_does_not_claim_consumption(self):
        with patch.object(bridge, "read_screen", return_value="› Ask Codex to do anything\nGPT-6 high"), \
             patch.object(bridge, "send_text") as send, patch.object(bridge, "send_key") as key, \
             patch.object(bridge.time, "sleep"):
            result = bridge.submit_text("peer", "STATUS: continuation")
        self.assertTrue(result["submitted"])
        self.assertFalse(result["confirmed"])
        send.assert_called_once()
        key.assert_called_once_with("peer", "enter")

    def test_user_draft_is_not_appended_or_cleared(self):
        with patch.object(bridge, "read_screen", return_value="› my unsent question\nGPT-6 high"), \
             patch.object(bridge, "send_text") as send, patch.object(bridge, "send_key") as key:
            with self.assertRaises(bridge.DispatchUnconfirmed) as error:
                bridge.submit_text("peer", "STATUS: continuation", marker="marker")
            self.assertEqual(error.exception.state, bridge.COMPOSE_OCCUPIED)
            send.assert_not_called()
            key.assert_not_called()

    def test_queued_message_is_one_send_not_a_failed_delivery_retry(self):
        screens = ["› Ask Codex to do anything\nGPT-6 high",
                   "Messages to be submitted after current tool\nmarker\n› Ask Codex to do anything\nGPT-6 high"]
        with patch.object(bridge, "read_screen", side_effect=screens), patch.object(bridge, "send_text") as send, \
             patch.object(bridge, "send_key") as key, patch.object(bridge.time, "sleep"):
            with self.assertRaises(bridge.DispatchUnconfirmed) as error:
                bridge.submit_text("peer", "STATUS: marker", marker="marker")
        self.assertEqual(error.exception.state, bridge.DELIVERY_QUEUED_AT_RECEIVER)
        send.assert_called_once()
        key.assert_called_once_with("peer", "enter")

    def test_current_provider_chrome_not_a_historical_banner(self):
        self.assertEqual(bridge.receiver_input_kind("❯\n[Opus 5] context 30%"), "AGENT_TUI")
        self.assertEqual(bridge.receiver_input_kind("[Opus 5] context 30%\n❯"), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
