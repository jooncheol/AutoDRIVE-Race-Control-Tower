# SPDX-License-Identifier: BSD-3-Clause

import unittest

from rct.bridge import (
    BridgeCache,
    BridgeRateTracker,
    OUTGOING_BRIDGE_DEFAULTS,
    extract_collision_counts,
)


class BridgeCacheTests(unittest.TestCase):
    def test_outgoing_cache_starts_with_control_defaults(self):
        cache = BridgeCache()

        self.assertEqual(cache.outgoing_cache, OUTGOING_BRIDGE_DEFAULTS)

    def test_outgoing_cache_merges_control_fields_only(self):
        cache = BridgeCache()

        outgoing = cache.update_outgoing(
            {
                "V1 Throttle": "0.5",
                "V1 Steering": "-0.1",
                "V1 Position": "ignored",
            }
        )

        self.assertEqual(outgoing["V1 Throttle"], "0.5")
        self.assertEqual(outgoing["V1 Steering"], "-0.1")
        self.assertNotIn("V1 Position", outgoing)
        self.assertEqual(outgoing["V2 Throttle"], "0.0")

    def test_current_outgoing_returns_copy(self):
        cache = BridgeCache()
        cache.update_outgoing({"V1 Throttle": "0.5"})

        outgoing = cache.current_outgoing()
        outgoing["V1 Throttle"] = "mutated"

        self.assertEqual(cache.current_outgoing()["V1 Throttle"], "0.5")

    def test_incoming_cache_keeps_latest_simulator_bridge_payload(self):
        cache = BridgeCache()
        payload = {"V1 Position": "1 2 3"}

        cached = cache.update_incoming(payload)
        payload["V1 Position"] = "mutated"

        self.assertEqual(cached, {"V1 Position": "1 2 3"})
        self.assertEqual(cache.incoming_cache, {"V1 Position": "1 2 3"})

    def test_current_incoming_returns_copy(self):
        cache = BridgeCache()
        cache.update_incoming({"V1 Position": "1 2 3"})

        incoming = cache.current_incoming()
        incoming["V1 Position"] = "mutated"

        self.assertEqual(cache.current_incoming(), {"V1 Position": "1 2 3"})

    def test_current_incoming_is_none_before_simulator_bridge_data(self):
        cache = BridgeCache()

        self.assertIsNone(cache.current_incoming())

    def test_bridge_outgoing_is_single_flight(self):
        cache = BridgeCache()

        self.assertTrue(cache.request_outgoing(1))
        self.assertFalse(cache.request_outgoing(2))
        self.assertEqual(cache.pending_response_count, 1)
        self.assertEqual(cache.queued_outgoing_count, 1)

        self.assertEqual(cache.complete_inflight(), {1})
        self.assertEqual(cache.pending_response_count, 0)

        self.assertEqual(cache.start_queued_outgoing(), {2})
        self.assertEqual(cache.pending_response_count, 1)
        self.assertEqual(cache.queued_outgoing_count, 0)
        self.assertEqual(cache.complete_inflight(), {2})

    def test_queued_outgoing_waits_for_inflight_completion(self):
        cache = BridgeCache()

        self.assertTrue(cache.request_outgoing(1))
        self.assertFalse(cache.request_outgoing(2))

        self.assertEqual(cache.start_queued_outgoing(), set())


class BridgeRateTrackerTests(unittest.TestCase):
    def test_tracks_rates_over_window(self):
        tracker = BridgeRateTracker(window_seconds=60.0)

        tracker.record(1, now=100.0)
        rates = tracker.record(1, now=101.0)

        self.assertAlmostEqual(rates["bridge_hz"], 2 / 60.0)
        self.assertEqual(rates["bridge_per_minute"], 2)

    def test_prunes_old_rates(self):
        tracker = BridgeRateTracker(window_seconds=60.0)

        tracker.record(1, now=0.0)
        rates = tracker.rates(1, now=61.0)

        self.assertEqual(rates["bridge_per_minute"], 0)


class CollisionCountTests(unittest.TestCase):
    def test_extracts_vehicle_collision_counts(self):
        counts = extract_collision_counts(
            {
                "V1 Collision Count": "1",
                "V2 collision_count": 2.0,
                "V1 Position": "ignored",
            }
        )

        self.assertEqual(counts, {1: 1, 2: 2})

    def test_ignores_non_numeric_collision_values(self):
        counts = extract_collision_counts({"V1 Collision Count": "n/a"})

        self.assertEqual(counts, {})


if __name__ == "__main__":
    unittest.main()
