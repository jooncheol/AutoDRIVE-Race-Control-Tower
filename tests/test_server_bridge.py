# SPDX-License-Identifier: BSD-3-Clause

import importlib.util
import asyncio
import unittest
from dataclasses import replace
from time import monotonic

from rct.config import Settings

AIOHTTP_AVAILABLE = importlib.util.find_spec("aiohttp") is not None
SOCKETIO_AVAILABLE = importlib.util.find_spec("socketio") is not None and AIOHTTP_AVAILABLE
RaceControlTower = None

if SOCKETIO_AVAILABLE:
    import socketio
if AIOHTTP_AVAILABLE:
    from aiohttp import web
    import aiohttp

    from rct.server import SOCKETIO_PATH, RaceControlTower


def test_settings() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=0,
        devkit_urls=("ws://127.0.0.1:4568", "ws://127.0.0.1:4569"),
        devkit_vehicle_ids=(1, 2),
        bridge_history_seconds=5.0,
        reconnect_delay_seconds=0.1,
        max_message_size=16 * 1024 * 1024,
        client_queue_size=8,
        ping_interval_seconds=20,
        ping_timeout_seconds=20,
        monitor_ws_hz=0.0,
        debug_engineio_messages=False,
        debug_engineio_max_chars=2000,
        debug_socketio_client=False,
        debug_engineio_client=False,
        debug_socketio_server=False,
        debug_engineio_server=False,
        debug_bridge_flow=False,
        log_bridge_messages=False,
        log_bridge_max_chars=20000,
        enable_origin=False,
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
    async def test_send_cached_incoming_bridge_replays_latest_bridge_for_devkit(self):
        tower = RaceControlTower(test_settings())
        devkit = tower.devkits[1]
        delivered = []

        async def enqueue(event, args):
            delivered.append((event, args))

        devkit.enqueue = enqueue

        await tower.bridge_history.append(
            {"V1 Position": "1 0 0", "V2 Position": "2 0 0", "V2 Throttle": "0.2"},
            now=monotonic(),
        )
        await tower.send_cached_incoming_bridge(devkit)

        self.assertEqual(
            delivered,
            [("Bridge", ({"V1 Position": "2 0 0", "V1 Throttle": "0.2"},))],
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
        original_url = tower.devkits[1].url

        await tower.configure_devkit(tower.devkits[0], "127.0.0.1", 4568, enabled=True)

        with self.assertRaisesRegex(ValueError, "already assigned"):
            await tower.configure_devkit(tower.devkits[1], "127.0.0.1", 4568, enabled=True)

        self.assertTrue(tower.devkits[1].configured)
        self.assertEqual(tower.devkits[1].url, original_url)

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_allows_duplicate_endpoint_when_previous_devkit_is_disabled(self):
        tower = RaceControlTower(test_settings())

        await tower.configure_devkit(tower.devkits[0], "127.0.0.1", 4568, enabled=False)
        await tower.configure_devkit(tower.devkits[1], "127.0.0.1", 4568, enabled=True)

        self.assertTrue(tower.devkits[1].configured)
        self.assertEqual(tower.devkits[1].url, "ws://127.0.0.1:4568")

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    @unittest.skipIf(not AIOHTTP_AVAILABLE, "aiohttp is not installed")
    async def test_monitor_rest_endpoint_update_applies_new_devkit_host_and_port(self):
        new_connected = asyncio.Event()

        devkit_sio = socketio.AsyncServer(async_mode="aiohttp")
        devkit_app = web.Application()
        devkit_sio.attach(devkit_app, socketio_path=SOCKETIO_PATH)

        async def devkit_connect(sid, environ):
            new_connected.set()
            return True

        devkit_sio.on("connect", devkit_connect)

        devkit_runner = web.AppRunner(devkit_app)
        await devkit_runner.setup()
        devkit_site = web.TCPSite(devkit_runner, "127.0.0.1", 0)
        await devkit_site.start()
        devkit_port = devkit_runner.addresses[0][1]

        settings = replace(
            test_settings(),
            devkit_urls=("ws://127.0.0.1:4568",),
            devkit_vehicle_ids=(1,),
            reconnect_delay_seconds=0.01,
        )
        tower = RaceControlTower(settings)
        tower.simulator_sids.add("simulator")
        tower.state.set_simulator_clients(1)
        tower_app = tower.create_app()
        tower_runner = web.AppRunner(tower_app)
        await tower_runner.setup()
        tower_site = web.TCPSite(tower_runner, "127.0.0.1", 0)
        await tower_site.start()
        tower_port = tower_runner.addresses[0][1]

        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"http://127.0.0.1:{tower_port}/monitor/REST/latest/devkits/1/endpoint",
                    json={
                        "host": "127.0.0.1",
                        "port": devkit_port,
                        "enabled": True,
                    },
                )
                self.assertEqual(response.status, 200)
                payload = await response.json()

            await asyncio.wait_for(new_connected.wait(), timeout=3)

            self.assertTrue(payload["ok"])
            self.assertEqual(tower.devkits[0].host, "127.0.0.1")
            self.assertEqual(tower.devkits[0].port, devkit_port)
            self.assertTrue(tower.devkits[0].configured)
            self.assertTrue(tower.devkits[0].enabled)
        finally:
            await tower.disconnect_all_devkits()
            await tower_runner.cleanup()
            await devkit_runner.cleanup()

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_bridge_rate_refresh_clears_stale_rates(self):
        tower = RaceControlTower(test_settings())
        devkit = tower.devkits[0]
        devkit.connected = True
        tower.state.set_devkit_connected(devkit.name, True)

        tower.bridge_rates.record(devkit.vehicle_id, now=100.0)
        tower.state.set_devkit_bridge_rate(devkit.name, 1.0, 60)
        tower.refresh_bridge_rates(now=101.1)

        snapshot = tower.state.snapshot()["devkits"][0]
        self.assertEqual(snapshot["bridge_hz"], 0.0)
        self.assertEqual(snapshot["bridge_per_minute"], 0)

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_simulator_bridge_immediately_echoes_latest_control_cache(self):
        tower = RaceControlTower(test_settings())
        emitted_to_simulator = []

        async def emit_to_simulators(event, args):
            emitted_to_simulator.append((event, args))

        async def broadcast_monitor(_message):
            return None

        tower.emit_to_simulators = emit_to_simulators
        tower.broadcast_monitor = broadcast_monitor
        await tower.control_cache.merge(
            {"V1 Throttle": "0.1", "V2 Steering": "0.2"},
            10.0,
            include_origin=False,
        )

        await tower.handle_simulator_bridge_event(
            "simulator",
            ({"V1 Position": "1 0 0", "V2 Position": "2 0 0"},),
        )

        self.assertEqual(len(emitted_to_simulator), 1)
        self.assertEqual(emitted_to_simulator[0][0], "Bridge")
        self.assertEqual(emitted_to_simulator[0][1][0]["V1 Throttle"], "0.1")
        self.assertEqual(emitted_to_simulator[0][1][0]["V2 Steering"], "0.2")
        self.assertNotIn("origin", emitted_to_simulator[0][1][0])
        latest = await tower.bridge_history.latest(now=10.0)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.payload["V1 Position"], "1 0 0")

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_devkit_control_updates_control_cache_and_sends_next_newer_bridge(self):
        tower = RaceControlTower(test_settings())
        devkit = tower.devkits[0]
        delivered_to_devkit = []

        async def enqueue(event, args):
            delivered_to_devkit.append((event, args))

        async def broadcast_monitor(_message):
            return None

        devkit.enqueue = enqueue
        tower.broadcast_monitor = broadcast_monitor

        await tower.bridge_history.append({"V1 Position": "old"})
        received_at = monotonic()
        await tower.bridge_history.append({"V1 Position": "next"}, now=received_at + 0.001)
        await tower.process_devkit_bridge_control(devkit, received_at, ({"V1 Throttle": "0.1"},))

        _timestamp, control_payload = await tower.control_cache.snapshot()
        self.assertEqual(control_payload["V1 Throttle"], "0.1")
        self.assertEqual(delivered_to_devkit, [("Bridge", ({"V1 Position": "next"},))])

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_devkit_bridge_outgoing_origin_is_opt_in(self):
        tower = RaceControlTower(replace(test_settings(), enable_origin=True))
        delivered_to_devkit = []

        async def enqueue(_event, _args):
            delivered_to_devkit.append(True)

        async def broadcast_monitor(_message):
            return None

        tower.broadcast_monitor = broadcast_monitor

        devkit = tower.devkits[0]
        devkit.enqueue = enqueue
        received_at = monotonic()
        await tower.bridge_history.append({"V1 Position": "next"}, now=received_at + 0.001)
        await tower.process_devkit_bridge_control(devkit, received_at, ({"V1 Throttle": "0.1"},))

        _timestamp, control_payload = await tower.control_cache.snapshot(include_origin=True)
        self.assertEqual(control_payload["origin"], 1)
        self.assertEqual(delivered_to_devkit, [True])

    @unittest.skipIf(not SOCKETIO_AVAILABLE, "python-socketio is not installed")
    async def test_cached_telemetry_message_contains_latest_vehicle_values(self):
        tower = RaceControlTower(test_settings())

        await tower.publish_simulator_telemetry(
            {"V1 Position": "1 2 0", "V2 Position": "3 4 0"},
            "Bridge",
        )
        await tower.publish_simulator_telemetry(
            {"V1 Speed": "5.5"},
            "Bridge",
        )

        message = tower.cached_telemetry_message()
        self.assertIsNotNone(message)
        self.assertIn('"1"', message)
        self.assertIn('"ips"', message)
        self.assertIn('"speed":5.5', message)
        self.assertIn('"2"', message)

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
