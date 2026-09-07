#!/usr/bin/env python3
"""Regression tests for executor unavailability and solo takeover."""

import unittest

import executor_availability as availability


class AvailabilityTests(unittest.TestCase):
    def test_full_billing_takeover_and_restore_path(self):
        state = availability.transition({}, "UNAVAILABLE_BILLING",
            executor_surface="surface:38", reason="insufficient balance")
        self.assertFalse(state["sentinel_allowed"])
        self.assertFalse(state["api_retry_allowed"])
        state = availability.transition(state, "SOLO_TAKEOVER",
            executor_surface="surface:38", reason="Codex assumes both roles")
        state = availability.transition(state, "HANDOFF_READY",
            executor_surface="surface:38", reason="safe boundary", handoff="/tmp/HANDOFF.md")
        state = availability.transition(state, "ACTIVE",
            executor_surface="surface:38", reason="user confirmed recovery")
        self.assertTrue(state["executor_dispatch_allowed"])
        self.assertEqual(state["executor_surface"], "surface:38")

    def test_cannot_skip_solo_takeover(self):
        state = availability.transition({}, "UNAVAILABLE_BILLING",
            executor_surface="surface:38", reason="quota")
        with self.assertRaisesRegex(ValueError, "invalid transition"):
            availability.transition(state, "ACTIVE", executor_surface="surface:38", reason="recovered")

    def test_direct_same_surface_recovery_without_solo_takeover(self):
        state = availability.transition({}, "UNAVAILABLE_BILLING",
            executor_surface="surface:38", reason="quota")
        state = availability.transition(state, "HANDOFF_READY",
            executor_surface="surface:38", reason="user confirmed recovery",
            handoff="/tmp/HANDOFF.md", recovery_confirmed=True)
        self.assertFalse(state["executor_dispatch_allowed"])
        state = availability.transition(state, "ACTIVE",
            executor_surface="surface:38", reason="handshake may resume")
        self.assertTrue(state["executor_dispatch_allowed"])
        self.assertNotIn("SOLO_TAKEOVER", [row["to"] for row in state["history"]])

    def test_direct_recovery_requires_explicit_confirmation(self):
        state = availability.transition({}, "UNAVAILABLE_BILLING",
            executor_surface="surface:38", reason="quota")
        with self.assertRaisesRegex(ValueError, "explicit user confirmation"):
            availability.transition(state, "HANDOFF_READY",
                executor_surface="surface:38", reason="looks healthy",
                handoff="/tmp/HANDOFF.md")

    def test_cannot_replace_context_bearing_surface(self):
        state = availability.transition({}, "UNAVAILABLE_BILLING",
            executor_surface="surface:38", reason="quota")
        with self.assertRaisesRegex(ValueError, "continuity violation"):
            availability.transition(state, "SOLO_TAKEOVER",
                executor_surface="surface:99", reason="replacement")

    def test_handoff_must_be_absolute(self):
        state = availability.transition({}, "UNAVAILABLE_BILLING",
            executor_surface="surface:38", reason="quota")
        state = availability.transition(state, "SOLO_TAKEOVER",
            executor_surface="surface:38", reason="takeover")
        with self.assertRaisesRegex(ValueError, "handoff path must be absolute"):
            availability.transition(state, "HANDOFF_READY",
                executor_surface="surface:38", reason="boundary", handoff="HANDOFF.md")


if __name__ == "__main__":
    unittest.main()
