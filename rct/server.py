from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .config import Settings, load_settings
from .protocol import rewrite_devkit_to_simulator, rewrite_simulator_to_devkit

LOGGER = logging.getLogger("rct")


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


async def safe_send(connection: ServerConnection, message: str | bytes) -> bool:
    try:
        await connection.send(message)
        return True
    except ConnectionClosed:
        return False


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

    async def run(self) -> None:
        while True:
            try:
                async with connect(
                    self.url,
                    max_size=self.settings.max_message_size,
                    ping_interval=self.settings.ping_interval_seconds,
                    ping_timeout=self.settings.ping_timeout_seconds,
                ) as websocket:
                    self.connected = True
                    LOGGER.info("%s connected to %s", self.name, self.url)
                    await self.tower.publish_status()
                    await self._run_connected(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("%s disconnected from %s: %s", self.name, self.url, exc)
            finally:
                if self.connected:
                    self.connected = False
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

    async def _receive_loop(self, websocket: Any) -> None:
        async for message in websocket:
            await self.tower.handle_devkit_message(self, message)


class RaceControlTower:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.simulators: set[ServerConnection] = set()
        self.frontends: set[ServerConnection] = set()
        self.devkits = [
            DevKitConnection(f"devkit:{index}", vehicle_id, url, settings, self)
            for index, (url, vehicle_id) in enumerate(
                zip(settings.devkit_urls, settings.devkit_vehicle_ids, strict=True),
                start=1,
            )
        ]

    async def start(self) -> None:
        devkit_tasks = [asyncio.create_task(devkit.run()) for devkit in self.devkits]
        try:
            async with serve(
                self.handle_client,
                self.settings.host,
                self.settings.port,
                max_size=self.settings.max_message_size,
                ping_interval=self.settings.ping_interval_seconds,
                ping_timeout=self.settings.ping_timeout_seconds,
            ) as server:
                sockets = ", ".join(str(socket.getsockname()) for socket in server.sockets or [])
                LOGGER.info("RCT WebSocket server listening on %s", sockets)
                await asyncio.Future()
        finally:
            for task in devkit_tasks:
                task.cancel()
            await asyncio.gather(*devkit_tasks, return_exceptions=True)

    async def handle_client(self, connection: ServerConnection) -> None:
        role = self._role_from_connection(connection)
        if role == "simulator":
            await self._handle_simulator(connection)
        elif role == "frontend":
            await self._handle_frontend(connection)
        else:
            await connection.send(
                envelope(
                    "error",
                    message="Use /simulator, /frontend, or ?role=simulator|frontend.",
                )
            )
            await connection.close(code=1008, reason="unknown RCT client role")

    def _role_from_connection(self, connection: ServerConnection) -> str | None:
        request_path = connection.request.path if connection.request else "/"
        parsed = urlparse(request_path)
        role_from_query = parse_qs(parsed.query).get("role", [None])[0]
        if role_from_query in {"simulator", "frontend"}:
            return role_from_query

        normalized_path = parsed.path.rstrip("/") or "/"
        if normalized_path == "/simulator":
            return "simulator"
        if normalized_path in {"/frontend", "/ws"}:
            return "frontend"
        return None

    async def _handle_simulator(self, connection: ServerConnection) -> None:
        self.simulators.add(connection)
        LOGGER.info("simulator connected from %s", connection.remote_address)
        await self.publish_status()
        try:
            async for message in connection:
                await self.handle_simulator_message(message)
        finally:
            self.simulators.discard(connection)
            LOGGER.info("simulator disconnected from %s", connection.remote_address)
            await self.publish_status()

    async def _handle_frontend(self, connection: ServerConnection) -> None:
        self.frontends.add(connection)
        LOGGER.info("frontend connected from %s", connection.remote_address)
        await safe_send(connection, self.status_message())
        try:
            async for message in connection:
                await self.handle_frontend_message(message)
        finally:
            self.frontends.discard(connection)
            LOGGER.info("frontend disconnected from %s", connection.remote_address)
            await self.publish_status()

    async def handle_simulator_message(self, message: str | bytes) -> None:
        targets: list[dict[str, Any]] = []
        for devkit in self.devkits:
            rewritten_message = rewrite_simulator_to_devkit(message, devkit.vehicle_id)
            if rewritten_message is None:
                continue
            await devkit.enqueue(rewritten_message)
            targets.append({"name": devkit.name, "vehicle_id": devkit.vehicle_id})

        await self.broadcast_frontend(
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
        await self.broadcast_frontend(
            envelope(
                "frame",
                source=devkit.name,
                vehicle_id=devkit.vehicle_id,
                target="simulator",
                **encode_message(rewritten_message),
            )
        )

    async def handle_frontend_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            await self.broadcast_frontend(
                envelope("error", source="frontend", message="binary frontend commands are not supported")
            )
            return

        try:
            command = json.loads(message)
        except json.JSONDecodeError:
            await self.broadcast_frontend(
                envelope("error", source="frontend", message="frontend command must be JSON")
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
                await self.broadcast_frontend(
                    envelope("error", source="frontend", message=f"unknown target {target!r}")
                )
                return
            rewritten_payload = rewrite_simulator_to_devkit(payload, devkit.vehicle_id)
            if rewritten_payload is not None:
                await devkit.enqueue(rewritten_payload)
        else:
            await self.broadcast_frontend(
                envelope("error", source="frontend", message=f"unsupported target {target!r}")
            )
            return

        await self.broadcast_frontend(envelope("command", source="frontend", target=target))

    def _get_devkit(self, name: str) -> DevKitConnection | None:
        return next((devkit for devkit in self.devkits if devkit.name == name), None)

    async def broadcast_simulators(self, message: str | bytes) -> None:
        await self._broadcast(self.simulators, message)

    async def broadcast_frontend(self, message: str) -> None:
        await self._broadcast(self.frontends, message)

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
        return envelope(
            "status",
            simulator_clients=len(self.simulators),
            frontend_clients=len(self.frontends),
            devkits=[
                {
                    "name": devkit.name,
                    "vehicle_id": devkit.vehicle_id,
                    "url": devkit.url,
                    "connected": devkit.connected,
                    "queued_messages": devkit.queue.qsize(),
                }
                for devkit in self.devkits
            ],
        )

    async def publish_status(self) -> None:
        await self.broadcast_frontend(self.status_message())


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
