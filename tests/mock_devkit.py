#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import argparse
import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from aiohttp import web
import socketio

LOGGER = logging.getLogger("mock_devkit")
DEFAULT_SOCKETIO_PATH = "socket.io"
DEFAULT_V1_PORT = 4568
DEFAULT_V2_PORT = 4569

CONTROL_MESSAGE_TEMPLATE: dict[str, str] = {
    "V1 Reset": "False",
    "V1 Throttle": "0.0",
    "V1 Steering": "0.0",
    "V2 Reset": "False",
    "V2 Throttle": "0.0",
    "V2 Steering": "0.0",
}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mock AutoDRIVE DevKit endpoints for RCT bias checks")
    parser.add_argument(
        "--mode",
        choices=("FIFO", "RR"),
        default="FIFO",
        help="FIFO responds from the devkit that received the Bridge event; RR alternates between V1 and V2",
    )
    parser.add_argument("--v1-port", type=int, default=DEFAULT_V1_PORT, help="Port for the V1 mock devkit")
    parser.add_argument("--v2-port", type=int, default=DEFAULT_V2_PORT, help="Port for the V2 mock devkit")
    parser.add_argument(
        "--socketio-path",
        default=DEFAULT_SOCKETIO_PATH,
        help="Socket.IO path used by the mock devkits",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )
    return parser


@dataclass
class EndpointState:
    vehicle_id: int
    port: int
    sid: str | None = None
    connected: bool = False


class MockDevKitCoordinator:
    def __init__(self, mode: str) -> None:
        self.mode = mode.upper()
        self._lock = asyncio.Lock()
        self._endpoints: dict[int, tuple[socketio.AsyncServer, EndpointState]] = {}
        self._rr_turn: int | None = None

    def register(self, vehicle_id: int, sio: socketio.AsyncServer, state: EndpointState) -> None:
        self._endpoints[vehicle_id] = (sio, state)

    def _connected_vehicle_ids(self) -> set[int]:
        return {
            vehicle_id
            for vehicle_id, (_sio, state) in self._endpoints.items()
            if state.connected
        }

    def _other_vehicle_id(self, vehicle_id: int) -> int:
        return 2 if vehicle_id == 1 else 1

    def _control_message(self, origin: int) -> dict[str, Any]:
        return {
            **CONTROL_MESSAGE_TEMPLATE,
            "origin": origin,
        }

    async def mark_connected(self, vehicle_id: int, sid: str) -> None:
        async with self._lock:
            _sio, state = self._endpoints[vehicle_id]
            state.sid = sid
            state.connected = True
            if self.mode == "RR" and self._rr_turn is None and len(self._connected_vehicle_ids()) >= 1:
                self._rr_turn = vehicle_id

    async def mark_disconnected(self, vehicle_id: int, sid: str) -> None:
        async with self._lock:
            _sio, state = self._endpoints[vehicle_id]
            if state.sid == sid:
                state.sid = None
            state.connected = False
            if self.mode == "RR" and self._rr_turn == vehicle_id:
                self._rr_turn = None

    async def handle_bridge(self, vehicle_id: int, sid: str, data: Any) -> None:
        async with self._lock:
            if self.mode == "FIFO":
                await self._emit_control(vehicle_id, sid, "FIFO")
                return

            if self._rr_turn is None:
                self._rr_turn = vehicle_id
            if self._rr_turn != vehicle_id:
                LOGGER.info("RR ignoring V%s Bridge event because turn is V%s", vehicle_id, self._rr_turn)
                return
            await self._emit_control(vehicle_id, sid, "RR")
            self._rr_turn = self._other_vehicle_id(vehicle_id)

    async def _emit_control(self, vehicle_id: int, sid: str, reason: str) -> None:
        sio, _state = self._endpoints[vehicle_id]
        payload = self._control_message(vehicle_id)
        LOGGER.info("sending Bridge control from V%s (%s): %s", vehicle_id, reason, payload)
        await sio._emit_internal(sid, "Bridge", deepcopy(payload), namespace="/")


async def serve_endpoint(
    coordinator: MockDevKitCoordinator,
    vehicle_id: int,
    port: int,
    socketio_path: str,
) -> None:
    sio = socketio.AsyncServer(async_mode="aiohttp", cors_allowed_origins="*")
    app = web.Application()
    sio.attach(app, socketio_path=socketio_path)
    state = EndpointState(vehicle_id=vehicle_id, port=port)
    coordinator.register(vehicle_id, sio, state)

    @sio.event
    async def connect(sid: str, environ: dict[str, Any], auth: Any = None) -> bool:
        peer = environ.get("REMOTE_ADDR", "unknown")
        LOGGER.info("V%s connected sid=%s from %s", vehicle_id, sid, peer)
        await coordinator.mark_connected(vehicle_id, sid)
        return True

    @sio.event
    async def disconnect(sid: str) -> None:
        LOGGER.info("V%s disconnected sid=%s", vehicle_id, sid)
        await coordinator.mark_disconnected(vehicle_id, sid)

    @sio.on("Bridge")
    async def bridge(sid: str, data: Any) -> None:
        LOGGER.info("V%s received Bridge from RCT: %s", vehicle_id, data)
        await coordinator.handle_bridge(vehicle_id, sid, data)

    async def root(_request: web.Request) -> web.Response:
        return web.Response(text=f"mock devkit V{vehicle_id} ready\n")

    app.router.add_get("/", root)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    LOGGER.info("mock devkit V%s listening on http://127.0.0.1:%s (%s)", vehicle_id, port, socketio_path)
    try:
        await asyncio.Future()
    finally:
        await runner.cleanup()


async def main() -> None:
    args = build_argument_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    coordinator = MockDevKitCoordinator(args.mode)

    tasks = [
        asyncio.create_task(serve_endpoint(coordinator, 1, args.v1_port, args.socketio_path), name="mock-devkit-v1"),
        asyncio.create_task(serve_endpoint(coordinator, 2, args.v2_port, args.socketio_path), name="mock-devkit-v2"),
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
