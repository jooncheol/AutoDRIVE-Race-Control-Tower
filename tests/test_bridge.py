# SPDX-License-Identifier: BSD-3-Clause

import asyncio
import unittest
import base64
import gzip

from rct.bridge import (
    BridgeHistory,
    BridgeRateTracker,
    ControlCache,
    OUTGOING_BRIDGE_DEFAULTS,
    extract_collision_counts,
    extract_lidar_range_arrays,
    extract_lidar_scans,
    extract_monitor_telemetry,
)


class BridgeHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_returns_newest_retained_payload(self):
        history = BridgeHistory(retention_seconds=5.0)

        await history.append({"V1 Position": "1 0 0"}, now=10.0)
        await history.append({"V1 Position": "2 0 0"}, now=12.0)

        latest = await history.latest(now=12.0)

        self.assertIsNotNone(latest)
        self.assertEqual(latest.timestamp, 12.0)
        self.assertEqual(latest.payload, {"V1 Position": "2 0 0"})

    async def test_retention_prunes_payloads_older_than_last_five_seconds(self):
        history = BridgeHistory(retention_seconds=5.0)

        await history.append({"old": 1}, now=10.0)
        await history.append({"new": 2}, now=16.0)

        record = await history.oldest_after(0.0, now=16.0)

        self.assertIsNotNone(record)
        self.assertEqual(record.payload, {"new": 2})

    async def test_wait_for_oldest_after_blocks_until_newer_payload_exists(self):
        history = BridgeHistory(retention_seconds=5.0)
        latest = await history.append({"V1 Position": "10 0 0"})
        waiter = asyncio.create_task(history.wait_for_oldest_after(latest.timestamp))

        await asyncio.sleep(0)
        self.assertFalse(waiter.done())

        record = await history.append({"V1 Position": "11 0 0"})

        waited_record = await asyncio.wait_for(waiter, timeout=1.0)
        self.assertEqual(waited_record.timestamp, record.timestamp)
        self.assertEqual(waited_record.payload, {"V1 Position": "11 0 0"})


class ControlCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_cache_starts_with_outgoing_defaults(self):
        cache = ControlCache()

        _timestamp, snapshot = await cache.snapshot()

        expected = dict(OUTGOING_BRIDGE_DEFAULTS)
        expected.pop("origin")
        self.assertEqual(snapshot, expected)

    async def test_merge_updates_control_fields_only(self):
        cache = ControlCache()

        _timestamp, snapshot = await cache.merge(
            {
                "V1 Throttle": "0.5",
                "V1 Steering": "-0.1",
                "V1 Position": "ignored",
            },
            100.0,
        )

        self.assertEqual(snapshot["V1 Throttle"], "0.5")
        self.assertEqual(snapshot["V1 Steering"], "-0.1")
        self.assertNotIn("V1 Position", snapshot)
        self.assertEqual(snapshot["V2 Throttle"], "0.0")

    async def test_merge_can_include_origin(self):
        cache = ControlCache()

        _timestamp, snapshot = await cache.merge(
            {"V2 Throttle": "0.5"},
            100.0,
            origin_vehicle_id=2,
            include_origin=True,
        )

        self.assertEqual(snapshot["origin"], 2)


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

    def test_default_window_is_one_second(self):
        tracker = BridgeRateTracker()

        tracker.record(1, now=100.0)
        rates = tracker.record(1, now=100.5)

        self.assertAlmostEqual(rates["bridge_hz"], 2.0)
        self.assertEqual(rates["bridge_per_minute"], 120)

    def test_rates_drop_to_zero_after_window_expires(self):
        tracker = BridgeRateTracker()

        tracker.record(1, now=100.0)
        rates = tracker.rates(1, now=101.1)

        self.assertEqual(rates["bridge_hz"], 0.0)
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


class MonitorTelemetryTests(unittest.TestCase):
    def test_extracts_only_monitor_telemetry_fields(self):
        telemetry = extract_monitor_telemetry(
            {
                "V1 Best Lap Time": "12.34",
                "V1 Collision Count": "2",
                "V1 Position": "1.5 -2.0 0.3",
                "V1 Lap Count": 4,
                "V1 Last Lap Time": "13.37",
                "V1 Speed": "5.5",
                "V1 Linear Velocity": "0.0 2.0 0.0",
                "V1 Orientation Quaternion": "0 0 0.7071068 0.7071068",
                "V1 Throttle": "ignored",
                "V2 collision_count": 1.0,
                "/autodrive/roboracer_2/ips": {"x": 7, "y": 8},
            }
        )

        self.assertEqual(telemetry[1]["best_lap_time"], "12.34")
        self.assertEqual(telemetry[1]["collision_count"], 2)
        self.assertEqual(telemetry[1]["ips"]["x"], 1.5)
        self.assertEqual(telemetry[1]["ips"]["y"], -2.0)
        self.assertEqual(telemetry[1]["lap_count"], 4)
        self.assertEqual(telemetry[1]["last_lap_count"], "13.37")
        self.assertEqual(telemetry[1]["speed"], 5.5)
        self.assertEqual(telemetry[1]["linear_velocity"], {"x": 0.0, "y": 2.0, "z": 0.0})
        self.assertAlmostEqual(telemetry[1]["heading_yaw"], 1.5707963267948966, places=6)
        self.assertEqual(
            telemetry[1]["orientation_quaternion"],
            {"x": 0.0, "y": 0.0, "z": 0.7071068, "w": 0.7071068},
        )
        self.assertNotIn("throttle", telemetry[1])
        self.assertEqual(telemetry[2]["collision_count"], 1)
        self.assertEqual(telemetry[2]["ips"]["x"], 7.0)
        self.assertEqual(telemetry[2]["ips"]["y"], 8.0)

    def test_extracts_monitor_telemetry_from_topic_message(self):
        telemetry = extract_monitor_telemetry(
            {
                "topic": "/autodrive/roboracer_2/ips",
                "payload": [3, 4, 0],
                "ignored": "value",
            }
        )

        self.assertEqual(telemetry[2]["ips"]["x"], 3.0)
        self.assertEqual(telemetry[2]["ips"]["y"], 4.0)

    def test_extracts_lidar_scan_points_for_traced_vehicle_only(self):
        scans = extract_lidar_scans(
            {
                "V1 LIDAR Scan": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
                "V2 LIDAR Scan": [{"x": 9, "y": 9}],
            },
            {1},
        )

        self.assertEqual(scans, {1: [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}]})

    def test_extracts_lidar_scan_from_topic_message(self):
        scans = extract_lidar_scans(
            {
                "topic": "/autodrive/roboracer_2/lidar",
                "payload": [[1, 2], [3, 4]],
            },
            {2},
        )

        self.assertEqual(scans[2][0], {"x": 1.0, "y": 2.0})
        self.assertEqual(scans[2][1], {"x": 3.0, "y": 4.0})

    def test_extracts_lidar_points_from_text_array(self):
        scans = extract_lidar_scans(
            {"V1 LIDAR Scan": "[1.0, 2.0, 3.0, 4.0]"},
            {1},
        )

        self.assertEqual(scans[1], [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}])

    def test_extracts_lidar_range_array_from_vehicle_origin(self):
        scans = extract_lidar_scans(
            {"V1 LIDAR Range Array": [1, 1, 1]},
            {1},
            {1: {"ips": {"x": 10, "y": 20}}},
        )

        self.assertEqual(len(scans[1]), 3)
        self.assertAlmostEqual(scans[1][1]["x"], 11.0)
        self.assertAlmostEqual(scans[1][1]["y"], 20.0)

    def test_extracts_raw_lidar_range_array_for_traced_vehicle(self):
        compressed_ranges = base64.b64encode(gzip.compress(b"1\n2\n3\n")).decode("ascii")
        arrays = extract_lidar_range_arrays(
            {
                "V1 LIDAR Range Array": compressed_ranges,
                "V2 LIDAR Range Array": [[9, 9]],
            },
            {1},
        )

        self.assertEqual(arrays, {1: [1.0, 2.0, 3.0]})


if __name__ == "__main__":
    unittest.main()
