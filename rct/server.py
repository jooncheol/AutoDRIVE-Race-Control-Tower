# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import socketio
from socketio import packet as socketio_packet
from aiohttp import WSMsgType, web

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
from .protocol import (
    DROP_VALUE,
    rewrite_devkit_payload_to_simulator,
    rewrite_simulator_payload_to_devkit,
)
from .state import DevKitMonitorState, RaceControlState
from .static_files import build_static_file_response

LOGGER = logging.getLogger("rct")
FRONTEND_ROOT = Path(__file__).resolve().parent.parent / "frontend"
SOCKETIO_PATH = "socket.io"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(event: str, **fields: Any) -> str:
    return json.dumps(
        {
            "event": event,
            "timestamp": utc_now(),
            **fields,
        },
        separators=(",", ":"),
    )


def normalize_socketio_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "ws":
        parsed = parsed._replace(scheme="http")
    elif parsed.scheme == "wss":
        parsed = parsed._replace(scheme="https")
    return urlunparse(parsed)


def socketio_data_from_args(args: tuple[Any, ...]) -> Any:
    if not args:
        return None
    if len(args) == 1:
        return args[0]
    return args


def encode_socketio_arg(value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "payload": base64.b64encode(value).decode("ascii"),
        }

    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return {"encoding": "text", "payload": str(value)}
    return {"encoding": "json", "payload": value}


def preview_debug_value(value: Any, max_chars: int) -> str:
    if isinstance(value, bytes):
        preview = f"<bytes len={len(value)}>"
    else:
        preview = str(value)

    if max_chars > 0 and len(preview) > max_chars:
        return f"{preview[:max_chars]}... <truncated {len(preview) - max_chars} chars>"
    return preview


def decode_monitor_arg(value: Any, encoding: str = "json") -> Any:
    if encoding == "base64":
        if not isinstance(value, str):
            raise ValueError("base64 payload must be a string")
        return base64.b64decode(value)
    if encoding == "text":
        return str(value)
    return value


def rewrite_args_for_devkit(args: tuple[Any, ...], vehicle_id: int) -> tuple[Any, ...] | None:
    rewritten: list[Any] = []
    for arg in args:
        item = rewrite_simulator_payload_to_devkit(arg, vehicle_id)
        if item is DROP_VALUE:
            return None
        rewritten.append(item)
    return tuple(rewritten)


def rewrite_args_for_simulator(args: tuple[Any, ...], vehicle_id: int) -> tuple[Any, ...]:
    return tuple(rewrite_devkit_payload_to_simulator(arg, vehicle_id) for arg in args)


@dataclass
class DevKitConnection:
    name: str
    vehicle_id: int
    url: str
    settings: Settings
    tower: "RaceControlTower"
    queue: asyncio.Queue[tuple[str, tuple[Any, ...]]] = field(init=False)
    client: socketio.AsyncClient = field(init=False)
    connected: bool = False
    _run_task: asyncio.Task[None] | None = None
    _send_task: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.settings.client_queue_size)
        self.client = socketio.AsyncClient(
            logger=False,
            engineio_logger=False,
            reconnection=False,
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        async def on_connect() -> None:
            self.tower.set_devkit_connected(self, True)
            LOGGER.info("%s connected to %s", self.name, self.url)
            await self.tower.publish_status()

        async def on_disconnect(*_: Any) -> None:
            if self.connected:
                self.tower.set_devkit_connected(self, False)
                LOGGER.info("%s disconnected from %s", self.name, self.url)
                await self.tower.publish_status()

        async def on_message(data: Any) -> None:
            print(data)
            await self.tower.handle_devkit_event(self, "message", (data,))

        async def on_any_event(event: str, *args: Any) -> None:
            await self.tower.handle_devkit_event(self, event, args)

        self.client.on("connect", on_connect)
        self.client.on("disconnect", on_disconnect)
        self.client.on("message", on_message)
        self.client.on("*", on_any_event)

    def start(self) -> None:
        if self._run_task is None or self._run_task.done():
            self._run_task = asyncio.create_task(self.run(), name=f"{self.name}:connect")
        if self._send_task is None or self._send_task.done():
            self._send_task = asyncio.create_task(self.send_loop(), name=f"{self.name}:send")

    async def stop(self) -> None:
        tasks = [task for task in (self._run_task, self._send_task) if task is not None]
        for task in tasks:
            task.cancel()

        if self.client.connected:
            await self.client.disconnect()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._run_task = None
        self._send_task = None
        if self.connected:
            self.tower.set_devkit_connected(self, False)
            await self.tower.publish_status()

    async def enqueue(self, event: str, args: tuple[Any, ...]) -> None:
        if self.queue.full():
            _ = self.queue.get_nowait()
            self.queue.task_done()
            LOGGER.warning("%s outbound queue full; dropped oldest event", self.name)

        await self.queue.put((event, args))
        self.tower.update_devkit_queue(self)

    async def run(self) -> None:
        while self.tower.has_simulators:
            if not self.client.connected:
                try:
                    await self.client.connect(
                        normalize_socketio_url(self.url),
                        transports=["websocket"],
                        socketio_path=SOCKETIO_PATH,
                        wait_timeout=self.settings.ping_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.tower.set_devkit_connected(self, False)
                    LOGGER.warning("%s could not connect to %s: %s", self.name, self.url, exc)

            await asyncio.sleep(self.settings.reconnect_delay_seconds)

    async def send_loop(self) -> None:
        while True:
            event, args = await self.queue.get()
            try:
                while not self.client.connected:
                    await asyncio.sleep(0.05)

                data = socketio_data_from_args(args)
                if event == "message":
                    await self.client.send(data)
                else:
                    await self.client.emit(event, data=data)
            finally:
                self.queue.task_done()
                self.tower.update_devkit_queue(self)


class RaceControlTower:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = RaceControlState()
        self.simulator_sids: set[str] = set()
        self.monitor_hub = MonitorEventHub()
        self.sio = socketio.AsyncServer(
            async_mode="aiohttp",
            cors_allowed_origins="*",
            logger=False,
            engineio_logger=False,
            max_http_buffer_size=settings.max_message_size or 100_000_000,
            ping_interval=settings.ping_interval_seconds,
            ping_timeout=settings.ping_timeout_seconds,
        )
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
        self._register_socketio_handlers()
        self._register_engineio_compat_handlers()

    @property
    def has_simulators(self) -> bool:
        return bool(self.simulator_sids)

    async def start(self) -> None:
        app = self.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.settings.host, self.settings.port)
        await site.start()
        LOGGER.info("RCT aiohttp/socket.io server listening on %s:%s", self.settings.host, self.settings.port)
        try:
            await asyncio.Future()
        finally:
            await self.disconnect_all_devkits()
            await runner.cleanup()

    def create_app(self) -> web.Application:
        app = web.Application(
            client_max_size=self.settings.max_message_size or 1024**3,
            middlewares=[self.log_socketio_request],
        )
        self.sio.attach(app, socketio_path=SOCKETIO_PATH)
        app.router.add_get("/monitor/WS/{version}", self.handle_monitor_ws)
        app.router.add_get("/monitor/REST/{version}", self.handle_monitor_rest)
        app.router.add_post(
            "/monitor/REST/{version}/devkits/{vehicle_id}/{action}",
            self.handle_monitor_devkit_command,
        )
        app.router.add_route("*", "/monitor/{tail:.*}", self.handle_unknown_monitor_path)
        app.router.add_get("/{tail:.*}", self.handle_static)
        return app

    @web.middleware
    async def log_socketio_request(
        self,
        request: web.Request,
        handler: web.RequestHandler,
    ) -> web.StreamResponse:
        if request.path.rstrip("/") == f"/{SOCKETIO_PATH}":
            LOGGER.info(
                "socket.io request remote=%s method=%s path=%s query=%s upgrade=%s",
                request.remote or "unknown",
                request.method,
                request.path,
                request.query_string,
                request.headers.get("Upgrade", ""),
            )
        return await handler(request)

    def _register_socketio_handlers(self) -> None:
        async def connect(sid: str, environ: dict[str, Any], auth: Any = None) -> bool:
            self.simulator_sids.add(sid)
            self.state.set_simulator_clients(len(self.simulator_sids))
            LOGGER.info("simulator connected via Socket.IO sid=%s", sid)
            self.connect_all_devkits()
            await self.publish_status()
            return True

        async def disconnect(sid: str, reason: str | None = None) -> None:
            self.simulator_sids.discard(sid)
            self.state.set_simulator_clients(len(self.simulator_sids))
            LOGGER.info("simulator disconnected sid=%s reason=%s", sid, reason)
            if not self.simulator_sids:
                await self.disconnect_all_devkits()
            await self.publish_status()

        async def message(sid: str, data: Any) -> None:
            await self.handle_simulator_event(sid, "message", (data,))

        async def any_event(event: str, sid: str, *args: Any) -> None:
            await self.handle_simulator_event(sid, event, args)

        self.sio.on("connect", connect)
        self.sio.on("disconnect", disconnect)
        self.sio.on("message", message)
        self.sio.on("*", any_event)

    def _register_engineio_compat_handlers(self) -> None:
        original_message_handler = self.sio.eio.handlers["message"]

        async def compat_message(eio_sid: str, data: Any) -> Any:
            if self.settings.debug_engineio_messages:
                LOGGER.info(
                    "engine.io message sid=%s data=%s",
                    eio_sid,
                    preview_debug_value(data, self.settings.debug_engineio_max_chars),
                )

            await self._ensure_socketio_namespace_for_event(eio_sid, data)
            result = original_message_handler(eio_sid, data)
            if inspect.isawaitable(result):
                return await result
            return result

        self.sio.eio.handlers["message"] = compat_message

    async def _ensure_socketio_namespace_for_event(self, eio_sid: str, data: Any) -> None:
        try:
            packet = self.sio.packet_class(encoded_packet=data)
        except (TypeError, ValueError):
            return

        if packet.packet_type != socketio_packet.EVENT:
            return

        namespace = packet.namespace or "/"
        sid = self.sio.manager.sid_from_eio_sid(eio_sid, namespace)
        if self.sio.manager.is_connected(sid, namespace):
            return

        event = packet.data[0] if isinstance(packet.data, list) and packet.data else "unknown"
        LOGGER.info(
            "implicit Socket.IO connect for event-before-connect eio_sid=%s namespace=%s event=%s",
            eio_sid,
            namespace,
            event,
        )
        await self.sio._handle_connect(eio_sid, namespace, None)

    async def handle_static(self, request: web.Request) -> web.Response:
        static_response = build_static_file_response(request.rel_url.raw_path, FRONTEND_ROOT)
        return web.Response(
            status=static_response.status_code,
            reason=static_response.reason_phrase,
            headers=dict(static_response.headers),
            body=static_response.body,
        )

    async def handle_unknown_monitor_path(self, request: web.Request) -> web.Response:
        return web.json_response({"error": "unsupported monitor protocol path"}, status=404)

    async def handle_monitor_rest(self, request: web.Request) -> web.Response:
        if not is_monitor_rest_path(request.path):
            return web.json_response({"error": "unsupported monitor protocol path"}, status=404)

        monitor_path = parse_monitor_path(request.path)
        if monitor_path is None:
            return web.json_response({"error": "unsupported monitor protocol path"}, status=404)

        return web.json_response(
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

    async def handle_monitor_devkit_command(self, request: web.Request) -> web.Response:
        version_path = f"/monitor/REST/{request.match_info['version']}"
        if not is_monitor_rest_path(version_path):
            return web.json_response({"error": "unsupported monitor protocol version"}, status=404)

        try:
            vehicle_id = int(request.match_info["vehicle_id"])
        except ValueError:
            return web.json_response({"error": "vehicle_id must be an integer"}, status=400)

        devkit = self._get_devkit_by_vehicle_id(vehicle_id)
        if devkit is None:
            return web.json_response({"error": f"unknown vehicle_id {vehicle_id}"}, status=404)

        action = request.match_info["action"]
        if action == "connect":
            devkit.start()
        elif action == "disconnect":
            await devkit.stop()
        else:
            return web.json_response({"error": f"unsupported devkit action {action!r}"}, status=404)

        await self.publish_status()
        return web.json_response({"ok": True, "state": self.status_payload()})

    async def handle_monitor_ws(self, request: web.Request) -> web.WebSocketResponse:
        if not is_monitor_ws_path(request.path):
            raise web.HTTPNotFound(text='{"error":"unsupported monitor protocol path"}')

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self.monitor_hub.add(ws)
        self.state.set_monitor_clients(self.monitor_hub.client_count)
        peer = request.remote or "unknown"
        LOGGER.info("monitor connected from %s", peer)
        await safe_send(ws, self.status_message())

        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await self.handle_monitor_message(message.data)
                elif message.type == WSMsgType.BINARY:
                    await self.broadcast_monitor(
                        envelope("error", source="monitor", message="binary monitor commands are not supported")
                    )
                elif message.type == WSMsgType.ERROR:
                    LOGGER.warning("monitor websocket error: %s", ws.exception())
        finally:
            self.monitor_hub.discard(ws)
            self.state.set_monitor_clients(self.monitor_hub.client_count)
            LOGGER.info("monitor disconnected from %s", peer)
            await self.publish_status()

        return ws

    def connect_all_devkits(self) -> None:
        for devkit in self.devkits:
            devkit.start()

    async def disconnect_all_devkits(self) -> None:
        await asyncio.gather(*(devkit.stop() for devkit in self.devkits), return_exceptions=True)

    async def handle_simulator_event(self, sid: str, event: str, args: tuple[Any, ...]) -> None:
        if sid not in self.simulator_sids:
            return

        targets: list[dict[str, Any]] = []
        for devkit in self.devkits:
            rewritten_args = rewrite_args_for_devkit(args, devkit.vehicle_id)
            if rewritten_args is None:
                continue
            await devkit.enqueue(event, rewritten_args)
            targets.append({"name": devkit.name, "vehicle_id": devkit.vehicle_id})

        await self.broadcast_monitor(
            envelope(
                "frame",
                source="simulator",
                socketio_event=event,
                targets=targets,
                args=[encode_socketio_arg(arg) for arg in args],
            )
        )

    async def handle_devkit_event(self, devkit: DevKitConnection, event: str, args: tuple[Any, ...]) -> None:
        rewritten_args = rewrite_args_for_simulator(args, devkit.vehicle_id)
        await self.emit_to_simulators(event, rewritten_args)
        await self.broadcast_monitor(
            envelope(
                "frame",
                source=devkit.name,
                vehicle_id=devkit.vehicle_id,
                target="simulator",
                socketio_event=event,
                args=[encode_socketio_arg(arg) for arg in rewritten_args],
            )
        )

    async def handle_monitor_message(self, message: str) -> None:
        try:
            command = json.loads(message)
        except json.JSONDecodeError:
            await self.broadcast_monitor(
                envelope("error", source="monitor", message="monitor command must be JSON")
            )
            return

        target = command.get("target", "simulator")
        event = command.get("event", "message")
        if not isinstance(event, str) or not event:
            await self.broadcast_monitor(
                envelope("error", source="monitor", message="monitor command event must be a non-empty string")
            )
            return

        try:
            args = self._command_args(command)
        except ValueError as exc:
            await self.broadcast_monitor(envelope("error", source="monitor", message=str(exc)))
            return

        if target == "simulator":
            await self.emit_to_simulators(event, args)
        elif target == "all-devkits":
            for devkit in self.devkits:
                rewritten_args = rewrite_args_for_devkit(args, devkit.vehicle_id)
                if rewritten_args is not None:
                    await devkit.enqueue(event, rewritten_args)
        elif isinstance(target, str) and target.startswith("devkit:"):
            devkit = self._get_devkit(target)
            if devkit is None:
                await self.broadcast_monitor(
                    envelope("error", source="monitor", message=f"unknown target {target!r}")
                )
                return
            rewritten_args = rewrite_args_for_devkit(args, devkit.vehicle_id)
            if rewritten_args is not None:
                await devkit.enqueue(event, rewritten_args)
        else:
            await self.broadcast_monitor(
                envelope("error", source="monitor", message=f"unsupported target {target!r}")
            )
            return

        await self.broadcast_monitor(envelope("command", source="monitor", target=target, socketio_event=event))

    def _command_args(self, command: dict[str, Any]) -> tuple[Any, ...]:
        if "args" in command:
            args = command["args"]
            if not isinstance(args, list):
                raise ValueError("monitor command args must be a list")
            return tuple(args)

        return (
            decode_monitor_arg(command.get("payload", ""), command.get("encoding", "json")),
        )

    async def emit_to_simulators(self, event: str, args: tuple[Any, ...]) -> None:
        data = socketio_data_from_args(args)
        for sid in tuple(self.simulator_sids):
            if event == "message":
                await self.sio.send(data, to=sid)
            else:
                await self.sio.emit(event, data=data, to=sid)

    def _get_devkit(self, name: str) -> DevKitConnection | None:
        return next((devkit for devkit in self.devkits if devkit.name == name), None)

    def _get_devkit_by_vehicle_id(self, vehicle_id: int) -> DevKitConnection | None:
        return next((devkit for devkit in self.devkits if devkit.vehicle_id == vehicle_id), None)

    async def broadcast_monitor(self, message: str) -> None:
        await self.monitor_hub.broadcast(message)
        self.state.set_monitor_clients(self.monitor_hub.client_count)

    def status_message(self) -> str:
        return envelope("status", **self.status_payload())

    def status_payload(self) -> dict[str, Any]:
        snapshot = self.state.snapshot()
        return {
            "monitor_protocol": {
                "name": "autodrive-rct-monitor",
                "version": MONITOR_PROTOCOL_VERSION,
            },
            "simulator_socketio_path": f"/{SOCKETIO_PATH}/",
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
