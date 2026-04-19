# SPDX-License-Identifier: BSD-3-Clause

import json
import unittest

from rct.protocol import (
    DROP_VALUE,
    rewrite_devkit_payload_to_simulator,
    rewrite_devkit_to_simulator,
    rewrite_simulator_payload_to_devkit,
    rewrite_simulator_to_devkit,
)


class ProtocolRewriteTests(unittest.TestCase):
    def test_rewrites_autodrive_bridge_fields_for_selected_vehicle(self):
        message = json.dumps(
            {
                "V1 Position": "1 1 0",
                "V1 Steering": "0.1",
                "V2 Position": "2 2 0",
                "V2 Steering": "0.2",
                "Reset": "False",
            }
        )

        rewritten = rewrite_simulator_to_devkit(message, vehicle_id=2)

        self.assertEqual(
            json.loads(rewritten),
            {
                "V1 Position": "2 2 0",
                "V1 Steering": "0.2",
                "Reset": "False",
            },
        )

    def test_rewrites_topic_values_for_selected_vehicle(self):
        message = json.dumps(
            {
                "topic": "/autodrive/roboracer_2/ips",
                "payload": {"frame_id": "roboracer_2"},
            }
        )

        rewritten = rewrite_simulator_to_devkit(message, vehicle_id=2)

        self.assertEqual(
            json.loads(rewritten),
            {
                "topic": "/autodrive/roboracer_1/ips",
                "payload": {"frame_id": "roboracer_1"},
            },
        )

    def test_drops_topic_values_for_other_vehicle(self):
        message = json.dumps(
            {
                "topic": "/autodrive/roboracer_1/ips",
                "payload": {"frame_id": "roboracer_1"},
            }
        )

        self.assertIsNone(rewrite_simulator_to_devkit(message, vehicle_id=2))

    def test_rewrites_devkit_commands_back_to_assigned_vehicle(self):
        message = json.dumps(
            {
                "topic": "/autodrive/roboracer_1/throttle_command",
                "payload": {"name": "roboracer_1", "value": 0.3},
            }
        )

        rewritten = rewrite_devkit_to_simulator(message, vehicle_id=2)

        self.assertEqual(
            json.loads(rewritten),
            {
                "topic": "/autodrive/roboracer_2/throttle_command",
                "payload": {"name": "roboracer_2", "value": 0.3},
            },
        )

    def test_rewrites_devkit_bridge_fields_back_to_assigned_vehicle(self):
        message = json.dumps({"V1 Throttle": "0.5", "V1 Steering": "-0.1"})

        rewritten = rewrite_devkit_to_simulator(message, vehicle_id=2)

        self.assertEqual(
            json.loads(rewritten),
            {"V2 Throttle": "0.5", "V2 Steering": "-0.1"},
        )

    def test_rewrites_socketio_dict_payload_without_serializing_shape(self):
        payload = {
            "V2 Position": "2 2 0",
            "topic": "/autodrive/roboracer_2/ips",
            "payload": {"frame_id": "roboracer_2"},
        }

        rewritten = rewrite_simulator_payload_to_devkit(payload, vehicle_id=2)

        self.assertEqual(
            rewritten,
            {
                "V1 Position": "2 2 0",
                "topic": "/autodrive/roboracer_1/ips",
                "payload": {"frame_id": "roboracer_1"},
            },
        )

    def test_drops_socketio_payload_for_other_vehicle(self):
        payload = {
            "topic": "/autodrive/roboracer_1/ips",
            "payload": {"frame_id": "roboracer_1"},
        }

        self.assertIs(rewrite_simulator_payload_to_devkit(payload, vehicle_id=2), DROP_VALUE)

    def test_rewrites_socketio_devkit_payload_back_to_simulator(self):
        payload = {"topic": "/autodrive/roboracer_1/throttle_command"}

        rewritten = rewrite_devkit_payload_to_simulator(payload, vehicle_id=2)

        self.assertEqual(rewritten, {"topic": "/autodrive/roboracer_2/throttle_command"})


if __name__ == "__main__":
    unittest.main()
