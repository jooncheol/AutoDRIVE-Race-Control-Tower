#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiohttp import web
import socketio

LOGGER = logging.getLogger("mock_simulator")
DEFAULT_SOCKETIO_PATH = "socket.io"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4568
DEFAULT_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
DEFAULT_SAMPLE_PATH = Path(__file__).with_name("bridge_sample.json")


def load_sample(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        sample = json.load(handle)
    if not isinstance(sample, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return sample


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mock AutoDRIVE bridge endpoint for RCT bias checks")
    parser.add_argument(
        "--mode",
        choices=("server", "client"),
        default="client",
        help="Run as a mock bridge server (default) or connect out as a client",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host in server mode")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port in server mode")
    parser.add_argument("--url", default=DEFAULT_URL, help="Target URL in client mode")
    parser.add_argument(
        "--sample",
        type=Path,
        default=DEFAULT_SAMPLE_PATH,
        help="Path to the bridge sample JSON payload",
    )
    parser.add_argument(
        "--socketio-path",
        default=DEFAULT_SOCKETIO_PATH,
        help="Socket.IO path used by the endpoint",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )
    return parser


async def run_client(url: str, socketio_path: str, sample: dict[str, Any]) -> None:
    client = socketio.AsyncClient(reconnection=False)
    origin_count = {1: 0, 2: 0}

    async def send_sample(reason: str) -> None:
        LOGGER.info("emit Bridge to %s (%s)", url, reason)
        await client.emit("Bridge", deepcopy(sample))

    @client.event
    async def connect() -> None:
        LOGGER.info("connected to %s", url)
        await send_sample("connect")

    @client.event
    async def disconnect() -> None:
        LOGGER.info("disconnected from %s", url)

    @client.on("Bridge")
    async def bridge(data: Any) -> None:
        if 'origin' in data:
            origin_count[data['origin']] += 1
            LOGGER.critical("V1: %08d, V2: %08d" % (origin_count[1], origin_count[2]))
        LOGGER.info("received Bridge from %s; replaying sample", url)
        await send_sample("bridge")

    await client.connect(url, transports=["websocket"], socketio_path=socketio_path)
    try:
        await asyncio.Future()
    finally:
        await client.disconnect()


async def main() -> None:
    args = build_argument_parser().parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sample = load_sample(args.sample)

    await run_client(args.url, args.socketio_path, sample)


if __name__ == "__main__":
    asyncio.run(main())
