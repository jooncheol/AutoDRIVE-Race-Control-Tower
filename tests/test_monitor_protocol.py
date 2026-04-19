# SPDX-License-Identifier: BSD-3-Clause

import unittest

from rct.monitor_protocol import (
    MONITOR_PROTOCOL_VERSION,
    MONITOR_REST_TRANSPORT,
    MONITOR_WS_TRANSPORT,
    is_monitor_rest_path,
    is_monitor_ws_path,
    parse_monitor_path,
)


class MonitorProtocolPathTests(unittest.TestCase):
    def test_accepts_versioned_rest_path(self):
        monitor_path = parse_monitor_path("/monitor/REST/0.1")

        self.assertIsNotNone(monitor_path)
        self.assertEqual(monitor_path.transport, MONITOR_REST_TRANSPORT)
        self.assertEqual(monitor_path.requested_version, MONITOR_PROTOCOL_VERSION)
        self.assertEqual(monitor_path.resolved_version, MONITOR_PROTOCOL_VERSION)
        self.assertTrue(is_monitor_rest_path("/monitor/REST/0.1"))

    def test_accepts_latest_ws_path(self):
        monitor_path = parse_monitor_path("/monitor/WS/latest")

        self.assertIsNotNone(monitor_path)
        self.assertEqual(monitor_path.transport, MONITOR_WS_TRANSPORT)
        self.assertEqual(monitor_path.requested_version, "latest")
        self.assertEqual(monitor_path.resolved_version, MONITOR_PROTOCOL_VERSION)
        self.assertTrue(is_monitor_ws_path("/monitor/WS/latest"))

    def test_rejects_unknown_version(self):
        self.assertIsNone(parse_monitor_path("/monitor/WS/9.9"))
        self.assertFalse(is_monitor_ws_path("/monitor/WS/9.9"))

    def test_rejects_unknown_transport(self):
        self.assertIsNone(parse_monitor_path("/monitor/SSE/0.1"))


if __name__ == "__main__":
    unittest.main()
