# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from .config import Settings, load_settings
from .monitor import MonitorEventHub, safe_send
from .monitor_protocol import (
    MONITOR_PROTOCOL_LATEST,
    MONITOR_PROTOCOL_VERSION,
    MONITOR_REST_TRANSPORT,
    MONITOR_WS_TRANSPORT,
    is_monitor_rest_path,
    is_monitor_ws_path,
    parse_monitor_path,
)
from .protocol import rewrite_devkit_to_simulator, rewrite_simulator_to_devkit
from .state import DevKitMonitorState, RaceControlState
from .static_files import build_static_file_response

LOGGER = logging.getLogger("rct")
FRONTEND_ROOT = Path(__file__).resolve().parent.parent / "frontend"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_message(message: str | bytes) -> dict[str, Any]:
    if isinstance(message, bytes):
        return {
            "encoding": "base64",
            "payload": base64.b64encode(message).decode("ascii"),
        }
    return {"encoding": "text", "payload": message}


def decode_payload(payload: Any, encoding: str = "text") -> str | bytes:
    if encoding == "base64":
        if not isinstance(payload, str):
            raise ValueError("base64 payload must be a string")
        return base64.b64decode(payload)
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, separators=(",", ":"))
    if isinstance(payload, bytes):
        return payload
    return str(payload)


def envelope(event: str, **fields: Any) -> str:
    return json.dumps(
        {
            "event": event,
            "timestamp": utc_now(),
            **fields,
        },
        separators=(",", ":"),
    )


@dataclass
class DevKitConnection:
    name: str
    vehicle_id: int
    url: str
    settings: Settings
    tower: "RaceControlTower"
    queue: asyncio.Queue[str | bytes] = field(init=False)
    connected: bool = False

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.settings.client_queue_size)

    async def enqueue(self, message: str | bytes) -> None:
        if self.queue.full():
            _ = self.queue.get_nowait()
            self.queue.task_done()
            LOGGER.warning("%s outbound queue full; dropped oldest message", self.name)
        await self.queue.put(message)
        self.tower.update_devkit_queue(self)

    async def run(self) -> None:
        while True:
            try:
                async with connect(
                    self.url,
                    max_size=self.settings.max_message_size,
                    ping_interval=self.settings.ping_interval_seconds,
                    ping_timeout=self.settings.ping_timeout_seconds,
                ) as websocket:
                    self.tower.set_devkit_connected(self, True)
                    LOGGER.info("%s connected to %s", self.name, self.url)
                    await self.tower.publish_status()
                    await self._run_connected(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("%s disconnected from %s: %s", self.name, self.url, exc)
            finally:
                if self.connected:
                    self.tower.set_devkit_connected(self, False)
                    await self.tower.publish_status()
            await asyncio.sleep(self.settings.reconnect_delay_seconds)

    async def _run_connected(self, websocket: Any) -> None:
        sender = asyncio.create_task(self._send_loop(websocket))
        receiver = asyncio.create_task(self._receive_loop(websocket))
        done, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()

    async def _send_loop(self, websocket: Any) -> None:
        while True:
            message = await self.queue.get()
            try:
                await websocket.send(message)
            finally:
                self.queue.task_done()
                self.tower.update_devkit_queue(self)

    async def _receive_loop(self, websocket: Any) -> None:
        async for message in websocket:
            await self.tower.handle_devkit_message(self, message)


class RaceControlTower:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = RaceControlState()
        self.simulators: set[ServerConnection] = set()
        self.monitor_hub = MonitorEventHub()
        self.devkits = [
            DevKitConnection(f"devkit:{index}", vehicle_id, url, settings, self)
            for index, (url, vehicle_id) in enumerate(
                zip(settings.devkit_urls, settings.devkit_vehicle_ids, strict=True),
                start=1,
            )
        ]
        self.state.configure_devkits(
            DevKitMonitorState(devkit.name, devkit.vehicle_id, devkit.url)
            for devkit in self.devkits
        )

    async def start(self) -> None:
        devkit_tasks = [asyncio.create_task(devkit.run()) for devkit in self.devkits]
        try:
            async with serve(
                self.handle_client,
                self.settings.host,
                self.settings.port,
                process_request=self.process_request,
                max_size=self.settings.max_message_size,
                ping_interval=self.settings.ping_interval_seconds,
                ping_timeout=self.settings.ping_timeout_seconds,
            ) as server:
                sockets = ", ".join(str(socket.getsockname()) for socket in server.sockets or [])
                LOGGER.info("RCT server listening on %s", sockets)
                await asyncio.Future()
        finally:
            for task in devkit_tasks:
                task.cancel()
            await asyncio.gather(*devkit_tasks, return_exceptions=True)

    async def handle_client(self, connection: ServerConnection) -> None:
        role = self._role_from_connection(connection)
        if role == "simulator":
            await self._handle_simulator(connection)
        elif role == "monitor":
            await self._handle_monitor(connection)
        else:
            await connection.send(
                envelope(
                    "error",
                    message="Use / for simulator or /monitor/WS/latest for monitor clients.",
                )
            )
            await connection.close(code=1008, reason="unknown RCT client role")

    def process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None

        if is_monitor_rest_path(request.path):
            return self.monitor_rest_response(request.path)
        if urlparse(request.path).path.startswith("/monitor/"):
            return self.json_response({"error": "unsupported monitor protocol path"}, status_code=404)

        static_response = build_static_file_response(request.path, FRONTEND_ROOT)
        headers = Headers()
        for name, value in static_response.headers:
            headers[name] = value
        return Response(
            static_response.status_code,
            static_response.reason_phrase,
            headers,
            static_response.body,
        )

    def monitor_rest_response(self, request_path: str) -> Response:
        monitor_path = parse_monitor_path(request_path)
        if monitor_path is None:
            return self.json_response({"error": "unsupported monitor protocol path"}, status_code=404)

        return self.json_response(
            {
                "protocol": "autodrive-rct-monitor",
                "transport": MONITOR_REST_TRANSPORT,
                "requested_version": monitor_path.requested_version,
                "version": monitor_path.resolved_version,
                "latest": MONITOR_PROTOCOL_VERSION,
                "aliases": {
                    "latest": f"/monitor/{MONITOR_REST_TRANSPORT}/{MONITOR_PROTOCOL_LATEST}",
                    "versioned": f"/monitor/{MONITOR_REST_TRANSPORT}/{MONITOR_PROTOCOL_VERSION}",
                    "events": f"/monitor/{MONITOR_WS_TRANSPORT}/{MONITOR_PROTOCOL_LATEST}",
                },
                "state": self.status_payload(),
            }
        )

    def json_response(self, payload: dict[str, Any], status_code: int = 200) -> Response:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = Headers()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        headers["X-Content-Type-Options"] = "nosniff"
        return Response(status_code, "OK" if status_code == 200 else "Not Found", headers, body)

    def _role_from_connection(self, connection: ServerConnection) -> str | None:
        request_path = connection.request.path if connection.request else "/"
        parsed = urlparse(request_path)

        normalized_path = parsed.path.rstrip("/") or "/"
        if normalized_path == "/":
            return "simulator"
        if is_monitor_ws_path(parsed.path):
            return "monitor"
        return None

    async def _handle_simulator(self, connection: ServerConnection) -> None:
        self.simulators.add(connection)
        self.state.set_simulator_clients(len(self.simulators))
        LOGGER.info("simulator connected from %s", connection.remote_address)
        await self.publish_status()
        try:
            async for message in connection:
                await self.handle_simulator_message(message)
        finally:
            self.simulators.discard(connection)
            self.state.set_simulator_clients(len(self.simulators))
            LOGGER.info("simulator disconnected from %s", connection.remote_address)
            await self.publish_status()

    async def _handle_monitor(self, connection: ServerConnection) -> None:
        self.monitor_hub.add(connection)
        self.state.set_monitor_clients(self.monitor_hub.client_count)
        LOGGER.info("monitor connected from %s", connection.remote_address)
        await safe_send(connection, self.status_message())
        try:
            async for message in connection:
                await self.handle_monitor_message(message)
        finally:
            self.monitor_hub.discard(connection)
            self.state.set_monitor_clients(self.monitor_hub.client_count)
            LOGGER.info("monitor disconnected from %s", connection.remote_address)
            await self.publish_status()

    async def handle_simulator_message(self, message: str | bytes) -> None:
        targets: list[dict[str, Any]] = []
        for devkit in self.devkits:
            rewritten_message = rewrite_simulator_to_devkit(message, devkit.vehicle_id)
            if rewritten_message is None:
                continue
            await devkit.enqueue(rewritten_message)
            targets.append({"name": devkit.name, "vehicle_id": devkit.vehicle_id})

        await self.broadcast_monitor(
            envelope(
                "frame",
                source="simulator",
                targets=targets,
                **encode_message(message),
            )
        )

    async def handle_devkit_message(self, devkit: DevKitConnection, message: str | bytes) -> None:
        rewritten_message = rewrite_devkit_to_simulator(message, devkit.vehicle_id)
        await self.broadcast_simulators(rewritten_message)
        await self.broadcast_monitor(
            envelope(
                "frame",
                source=devkit.name,
                vehicle_id=devkit.vehicle_id,
                target="simulator",
                **encode_message(rewritten_message),
            )
        )

    async def handle_monitor_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            await self.broadcast_monitor(
                envelope("error", source="monitor", message="binary monitor commands are not supported")
            )
            return

        try:
            command = json.loads(message)
        except json.JSONDecodeError:
            await self.broadcast_monitor(
                envelope("error", source="monitor", message="monitor command must be JSON")
            )
            return

        target = command.get("target", "simulator")
        payload = decode_payload(command.get("payload", ""), command.get("encoding", "text"))

        if target == "simulator":
            await self.broadcast_simulators(payload)
        elif target == "all-devkits":
            for devkit in self.devkits:
                rewritten_payload = rewrite_simulator_to_devkit(payload, devkit.vehicle_id)
                if rewritten_payload is not None:
                    await devkit.enqueue(rewritten_payload)
        elif isinstance(target, str) and target.startswith("devkit:"):
            devkit = self._get_devkit(target)
            if devkit is None:
                await self.broadcast_monitor(
                    envelope("error", source="monitor", message=f"unknown target {target!r}")
                )
                return
            rewritten_payload = rewrite_simulator_to_devkit(payload, devkit.vehicle_id)
            if rewritten_payload is not None:
                await devkit.enqueue(rewritten_payload)
        else:
            await self.broadcast_monitor(
                envelope("error", source="monitor", message=f"unsupported target {target!r}")
            )
            return

        await self.broadcast_monitor(envelope("command", source="monitor", target=target))

    def _get_devkit(self, name: str) -> DevKitConnection | None:
        return next((devkit for devkit in self.devkits if devkit.name == name), None)

    async def broadcast_simulators(self, message: str | bytes) -> None:
        await self._broadcast(self.simulators, message)

    async def broadcast_monitor(self, message: str) -> None:
        await self.monitor_hub.broadcast(message)
        self.state.set_monitor_clients(self.monitor_hub.client_count)

    async def _broadcast(self, clients: set[ServerConnection], message: str | bytes) -> None:
        if not clients:
            return
        snapshot = tuple(clients)
        results = await asyncio.gather(
            *(safe_send(client, message) for client in snapshot),
            return_exceptions=True,
        )
        for client, result in zip(snapshot, results, strict=False):
            if result is False or isinstance(result, Exception):
                clients.discard(client)

    def status_message(self) -> str:
        return envelope("status", **self.status_payload())

    def status_payload(self) -> dict[str, Any]:
        snapshot = self.state.snapshot()
        return {
            "monitor_protocol": {
                "name": "autodrive-rct-monitor",
                "version": MONITOR_PROTOCOL_VERSION,
            },
            **snapshot,
        }

    async def publish_status(self) -> None:
        await self.broadcast_monitor(self.status_message())

    def set_devkit_connected(self, devkit: DevKitConnection, connected: bool) -> None:
        devkit.connected = connected
        self.state.set_devkit_connected(devkit.name, connected)
        self.update_devkit_queue(devkit)

    def update_devkit_queue(self, devkit: DevKitConnection) -> None:
        self.state.set_devkit_queue_size(devkit.name, devkit.queue.qsize())


async def async_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    tower = RaceControlTower(settings)
    await tower.start()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("RCT stopped")
