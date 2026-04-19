# SPDX-License-Identifier: BSD-3-Clause

import unittest

from rct.state import DevKitMonitorState, RaceControlState


class RaceControlStateTests(unittest.TestCase):
    def test_snapshot_contains_shared_monitor_state(self):
        state = RaceControlState()
        state.configure_devkits(
            [
                DevKitMonitorState("devkit:1", 1, "ws://127.0.0.1:4568"),
                DevKitMonitorState("devkit:2", 2, "ws://127.0.0.1:4569"),
            ]
        )

        state.set_simulator_clients(1)
        state.set_monitor_clients(2)
        state.set_devkit_connected("devkit:1", True)
        state.set_devkit_queue_size("devkit:1", 3)

        snapshot = state.snapshot()

        self.assertEqual(snapshot["simulator_clients"], 1)
        self.assertEqual(snapshot["monitor_clients"], 2)
        self.assertEqual(
            snapshot["devkits"][0],
            {
                "name": "devkit:1",
                "vehicle_id": 1,
                "url": "ws://127.0.0.1:4568",
                "host": "",
                "port": None,
                "configured": False,
                "enabled": True,
                "connected": True,
                "queued_messages": 3,
                "bridge_hz": 0.0,
                "bridge_per_minute": 0,
            },
        )

    def test_snapshot_tracks_devkit_endpoint_and_enabled_state(self):
        state = RaceControlState()
        state.configure_devkits([DevKitMonitorState("devkit:1", 1, "")])

        state.set_devkit_endpoint("devkit:1", "ws://10.0.2.2:4568", "10.0.2.2", 4568, True)
        state.set_devkit_enabled("devkit:1", False)

        snapshot = state.snapshot()

        self.assertEqual(snapshot["devkits"][0]["url"], "ws://10.0.2.2:4568")
        self.assertEqual(snapshot["devkits"][0]["host"], "10.0.2.2")
        self.assertEqual(snapshot["devkits"][0]["port"], 4568)
        self.assertTrue(snapshot["devkits"][0]["configured"])
        self.assertFalse(snapshot["devkits"][0]["enabled"])

    def test_snapshot_tracks_devkit_bridge_rate(self):
        state = RaceControlState()
        state.configure_devkits([DevKitMonitorState("devkit:1", 1, "")])

        state.set_devkit_bridge_rate("devkit:1", 0.5, 30)

        snapshot = state.snapshot()

        self.assertEqual(snapshot["devkits"][0]["bridge_hz"], 0.5)
        self.assertEqual(snapshot["devkits"][0]["bridge_per_minute"], 30)

    def test_revision_changes_only_when_values_change(self):
        state = RaceControlState()
        state.configure_devkits([DevKitMonitorState("devkit:1", 1, "ws://127.0.0.1:4568")])
        first_revision = state.snapshot()["revision"]

        state.set_monitor_clients(0)
        self.assertEqual(state.snapshot()["revision"], first_revision)

        state.set_monitor_clients(1)
        self.assertGreater(state.snapshot()["revision"], first_revision)


if __name__ == "__main__":
    unittest.main()
