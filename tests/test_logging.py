# SPDX-License-Identifier: BSD-3-Clause

import importlib.util
import logging
import unittest

from rct.config import Settings


SOCKETIO_AVAILABLE = importlib.util.find_spec("socketio") is not None

if SOCKETIO_AVAILABLE:
    from rct.server import configure_socketio_logging
else:
    configure_socketio_logging = None


def test_settings(**overrides) -> Settings:
    values = {
        "host": "127.0.0.1",
        "port": 0,
        "devkit_urls": (),
        "devkit_vehicle_ids": (),
        "reconnect_delay_seconds": 0.1,
        "max_message_size": 16 * 1024 * 1024,
        "client_queue_size": 8,
        "ping_interval_seconds": 20,
        "ping_timeout_seconds": 20,
        "monitor_ws_hz": 0.0,
        "debug_engineio_messages": False,
        "debug_engineio_max_chars": 2000,
        "debug_socketio_client": False,
        "debug_engineio_client": False,
        "debug_socketio_server": False,
        "debug_engineio_server": False,
        "debug_bridge_flow": False,
        "log_bridge_messages": False,
        "log_bridge_max_chars": 20000,
        "enable_origin": False,
    }
    values.update(overrides)
    return Settings(**values)


class SocketIoLoggingTests(unittest.TestCase):
    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    def test_socketio_engineio_loggers_are_quiet_by_default(self):
        configure_socketio_logging(test_settings())

        self.assertEqual(logging.getLogger("socketio.client").level, logging.WARNING)
        self.assertEqual(logging.getLogger("engineio.client").level, logging.WARNING)
        self.assertEqual(logging.getLogger("socketio.server").level, logging.WARNING)
        self.assertEqual(logging.getLogger("engineio.server").level, logging.WARNING)

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    def test_debug_flags_enable_selected_library_logger(self):
        configure_socketio_logging(test_settings(debug_engineio_server=True))

        self.assertEqual(logging.getLogger("engineio.server").level, logging.INFO)
        self.assertEqual(logging.getLogger("socketio.server").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
