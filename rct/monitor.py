# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import asyncio

from aiohttp import web


async def safe_send(connection: web.WebSocketResponse, message: str | bytes) -> bool:
    if connection.closed:
        return False

    try:
        if isinstance(message, bytes):
            await connection.send_bytes(message)
        else:
            await connection.send_str(message)
        return True
    except (ConnectionResetError, RuntimeError):
        return False


class MonitorEventHub:
    def __init__(self) -> None:
        self.clients: set[web.WebSocketResponse] = set()

    @property
    def client_count(self) -> int:
        return len(self.clients)

    def add(self, connection: web.WebSocketResponse) -> None:
        self.clients.add(connection)

    def discard(self, connection: web.WebSocketResponse) -> None:
        self.clients.discard(connection)

    async def broadcast(self, message: str) -> None:
        if not self.clients:
            return

        snapshot = tuple(self.clients)
        results = await asyncio.gather(
            *(safe_send(client, message) for client in snapshot),
            return_exceptions=True,
        )
        for client, result in zip(snapshot, results, strict=False):
            if result is False or isinstance(result, Exception):
                self.clients.discard(client)
