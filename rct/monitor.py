# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import asyncio

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed


async def safe_send(connection: ServerConnection, message: str | bytes) -> bool:
    try:
        await connection.send(message)
        return True
    except ConnectionClosed:
        return False


class MonitorEventHub:
    def __init__(self) -> None:
        self.clients: set[ServerConnection] = set()

    @property
    def client_count(self) -> int:
        return len(self.clients)

    def add(self, connection: ServerConnection) -> None:
        self.clients.add(connection)

    def discard(self, connection: ServerConnection) -> None:
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
