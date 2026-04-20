# SPDX-License-Identifier: BSD-3-Clause

import importlib.util
import asyncio
import unittest
from dataclasses import replace

from rct.config import Settings

SOCKETIO_AVAILABLE = importlib.util.find_spec("socketio") is not None

if SOCKETIO_AVAILABLE:
    import socketio
    from aiohttp import web

    from rct.server import SOCKETIO_PATH, RaceControlTower
else:
    RaceControlTower = None


def test_settings() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=0,
        devkit_urls=("ws://127.0.0.1:4568", "ws://127.0.0.1:4569"),
        devkit_vehicle_ids=(1, 2),
        reconnect_delay_seconds=0.1,
        max_message_size=16 * 1024 * 1024,
        client_queue_size=8,
        ping_interval_seconds=20,
        ping_timeout_seconds=20,
        debug_engineio_messages=False,
        debug_engineio_max_chars=2000,
        debug_socketio_client=False,
        debug_engineio_client=False,
        debug_socketio_server=False,
        debug_engineio_server=False,
        debug_bridge_flow=False,
        log_bridge_messages=False,
        log_bridge_max_chars=20000,
    )


class ServerBridgeFlowTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_emit_to_simulators_avoids_socketio_4_asyncio_wait_coroutine_bug(self):
        received = []
        received_event = asyncio.Event()
        settings = replace(test_settings(), devkit_urls=(), devkit_vehicle_ids=())
        tower = RaceControlTower(settings)
        tower_app = tower.create_app()
        tower_runner = web.AppRunner(tower_app)
        await tower_runner.setup()
        tower_site = web.TCPSite(tower_runner, "127.0.0.1", 0)
        await tower_site.start()
        tower_port = tower_runner.addresses[0][1]

        simulator = socketio.AsyncClient(reconnection=False)

        async def simulator_bridge(data):
            received.append(data)
            received_event.set()

        simulator.on("Bridge", simulator_bridge)

        try:
            await simulator.connect(
                f"http://127.0.0.1:{tower_port}",
                transports=["websocket"],
                socketio_path=SOCKETIO_PATH,
            )

            await tower.emit_to_simulators("Bridge", ({"V1 Throttle": "0.1"},))
            await asyncio.wait_for(received_event.wait(), timeout=3)
        finally:
            if getattr(simulator.eio, "state", "disconnected") == "connected":
                await simulator.disconnect()
            await tower_runner.cleanup()

        self.assertEqual(received, [{"V1 Throttle": "0.1"}])

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_socketio_bridge_event_reaches_connected_devkit(self):
        received_by_devkit = []
        received_event = asyncio.Event()

        devkit_sio = socketio.AsyncServer(async_mode="aiohttp")
        devkit_app = web.Application()
        devkit_sio.attach(devkit_app, socketio_path=SOCKETIO_PATH)

        async def devkit_connect(sid, environ):
            return True

        async def devkit_bridge(sid, data):
            received_by_devkit.append(data)
            received_event.set()

        devkit_sio.on("connect", devkit_connect)
        devkit_sio.on("Bridge", devkit_bridge)

        devkit_runner = web.AppRunner(devkit_app)
        await devkit_runner.setup()
        devkit_site = web.TCPSite(devkit_runner, "127.0.0.1", 0)
        await devkit_site.start()
        devkit_port = devkit_runner.addresses[0][1]

        settings = replace(
            test_settings(),
            devkit_urls=(f"ws://127.0.0.1:{devkit_port}",),
            devkit_vehicle_ids=(2,),
            reconnect_delay_seconds=0.01,
        )
        tower = RaceControlTower(settings)
        self.assertTrue(tower.devkits[0].configured)
        self.assertEqual(tower.devkits[0].host, "127.0.0.1")
        self.assertEqual(tower.devkits[0].port, devkit_port)
        tower_app = tower.create_app()
        tower_runner = web.AppRunner(tower_app)
        await tower_runner.setup()
        tower_site = web.TCPSite(tower_runner, "127.0.0.1", 0)
        await tower_site.start()
        tower_port = tower_runner.addresses[0][1]

        simulator = socketio.AsyncClient(reconnection=False)
        try:
            await simulator.connect(
                f"http://127.0.0.1:{tower_port}",
                transports=["websocket"],
                socketio_path=SOCKETIO_PATH,
            )

            for _ in range(200):
                if tower.devkits[0].connected:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(tower.devkits[0].connected)

            await simulator.emit(
                "Bridge",
                {
                    "V1 Position": "1 0 0",
                    "V2 Position": "2 0 0",
                    "V2 Throttle": "0.2",
                },
            )

            await asyncio.wait_for(received_event.wait(), timeout=3)
        finally:
            if getattr(simulator.eio, "state", "disconnected") == "connected":
                await simulator.disconnect()
            await tower.disconnect_all_devkits()
            await tower_runner.cleanup()
            await devkit_runner.cleanup()

        self.assertEqual(
            received_by_devkit,
            [{"V1 Position": "2 0 0", "V1 Throttle": "0.2"}],
        )

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_socketio_4_default_namespace_connection_uses_engineio_state(self):
        tower = RaceControlTower(test_settings())
        devkit = tower.devkits[0]

        devkit.client.namespaces = []
        devkit.client.eio.state = "connected"

        self.assertTrue(devkit._client_connected())

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_engineio_ping_timeout_is_used_as_server_grace_period(self):
        tower = RaceControlTower(test_settings())

        self.assertEqual(tower.sio.eio.ping_interval, 20)
        self.assertEqual(tower.sio.eio.ping_interval_grace_period, 20)
        self.assertEqual(tower.sio.eio.ping_timeout, 20)

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_rejects_duplicate_active_devkit_endpoint(self):
        tower = RaceControlTower(test_settings())

        await tower.configure_devkit(tower.devkits[0], "127.0.0.1", 4568, enabled=True)

        with self.assertRaisesRegex(ValueError, "already assigned"):
            await tower.configure_devkit(tower.devkits[1], "127.0.0.1", 4568, enabled=True)

        self.assertFalse(tower.devkits[1].configured)

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_allows_duplicate_endpoint_when_previous_devkit_is_disabled(self):
        tower = RaceControlTower(test_settings())

        await tower.configure_devkit(tower.devkits[0], "127.0.0.1", 4568, enabled=False)
        await tower.configure_devkit(tower.devkits[1], "127.0.0.1", 4568, enabled=True)

        self.assertTrue(tower.devkits[1].configured)
        self.assertEqual(tower.devkits[1].url, "ws://127.0.0.1:4568")

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_devkit_bridge_to_simulator_is_single_flight(self):
        tower = RaceControlTower(test_settings())
        emitted_to_simulator = []
        delivered_to_devkit = []

        async def emit_to_simulators(event, args):
            emitted_to_simulator.append((event, args))

        async def broadcast_monitor(_message):
            return None

        tower.emit_to_simulators = emit_to_simulators
        tower.broadcast_monitor = broadcast_monitor

        for devkit in tower.devkits:
            devkit.configured = True
            devkit.connected = True

            async def enqueue(event, args, *, current_devkit=devkit):
                delivered_to_devkit.append((current_devkit.vehicle_id, event, args))

            devkit.enqueue = enqueue

        await tower.handle_devkit_bridge_event(tower.devkits[0], ({"V1 Throttle": "0.1"},))
        await tower.handle_devkit_bridge_event(tower.devkits[1], ({"V1 Throttle": "0.2"},))

        self.assertEqual(len(emitted_to_simulator), 1)
        self.assertEqual(emitted_to_simulator[0][0], "Bridge")
        self.assertEqual(emitted_to_simulator[0][1][0]["V1 Throttle"], "0.1")
        self.assertEqual(emitted_to_simulator[0][1][0]["V2 Throttle"], "0.0")
        self.assertEqual(tower.bridge_cache.pending_response_count, 1)
        self.assertEqual(tower.bridge_cache.queued_outgoing_count, 1)

        await tower.handle_simulator_bridge_event(
            "simulator",
            ({"V1 Position": "1 0 0", "V2 Position": "2 0 0"},),
        )

        self.assertEqual(delivered_to_devkit[0][0], 1)
        self.assertEqual(len(emitted_to_simulator), 2)
        self.assertEqual(emitted_to_simulator[1][0], "Bridge")
        self.assertEqual(emitted_to_simulator[1][1][0]["V1 Throttle"], "0.1")
        self.assertEqual(emitted_to_simulator[1][1][0]["V2 Throttle"], "0.2")
        self.assertEqual(tower.bridge_cache.pending_response_count, 1)
        self.assertEqual(tower.bridge_cache.queued_outgoing_count, 0)

        await tower.handle_simulator_bridge_event(
            "simulator",
            ({"V1 Position": "1 1 0", "V2 Position": "2 1 0"},),
        )

        self.assertEqual(delivered_to_devkit[1][0], 2)
        self.assertEqual(len(emitted_to_simulator), 2)
        self.assertEqual(tower.bridge_cache.pending_response_count, 0)

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_event_before_connect_implicitly_connects_socketio_4_namespace(self):
        tower = RaceControlTower(test_settings())
        eio_sid = "simulator-eio-sid"

        async def send_packet(_sid, _packet):
            return None

        async def broadcast_monitor(_message):
            return None

        tower.sio._send_packet = send_packet
        tower.broadcast_monitor = broadcast_monitor
        tower.connect_all_devkits = lambda: None
        tower.sio.environ[eio_sid] = {}

        await tower._ensure_socketio_namespace_for_event(
            eio_sid,
            '2["Bridge",{"V1 Throttle":"0.1"}]',
        )

        self.assertTrue(tower.sio.manager.is_connected(eio_sid, "/"))
        self.assertIn(eio_sid, tower.simulator_sids)


if __name__ == "__main__":
    unittest.main()
