# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any, Iterable


@dataclass
class DevKitMonitorState:
    name: str
    vehicle_id: int
    url: str
    host: str = ""
    port: int | None = None
    configured: bool = False
    enabled: bool = True
    connected: bool = False
    queued_messages: int = 0


class RaceControlState:
    def __init__(self) -> None:
        self._lock = RLock()
        self._revision = 0
        self._simulator_clients = 0
        self._monitor_clients = 0
        self._devkits: dict[str, DevKitMonitorState] = {}

    def configure_devkits(self, devkits: Iterable[DevKitMonitorState]) -> None:
        with self._lock:
            self._devkits = {devkit.name: devkit for devkit in devkits}
            self._revision += 1

    def set_simulator_clients(self, count: int) -> None:
        with self._lock:
            if self._simulator_clients == count:
                return
            self._simulator_clients = count
            self._revision += 1

    def set_monitor_clients(self, count: int) -> None:
        with self._lock:
            if self._monitor_clients == count:
                return
            self._monitor_clients = count
            self._revision += 1

    def set_devkit_connected(self, name: str, connected: bool) -> None:
        with self._lock:
            if self._devkits[name].connected == connected:
                return
            self._devkits[name].connected = connected
            self._revision += 1

    def set_devkit_endpoint(
        self,
        name: str,
        url: str,
        host: str,
        port: int,
        configured: bool,
    ) -> None:
        with self._lock:
            devkit = self._devkits[name]
            if (
                devkit.url == url
                and devkit.host == host
                and devkit.port == port
                and devkit.configured == configured
            ):
                return
            devkit.url = url
            devkit.host = host
            devkit.port = port
            devkit.configured = configured
            self._revision += 1

    def set_devkit_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            if self._devkits[name].enabled == enabled:
                return
            self._devkits[name].enabled = enabled
            self._revision += 1

    def set_devkit_queue_size(self, name: str, queued_messages: int) -> None:
        with self._lock:
            if self._devkits[name].queued_messages == queued_messages:
                return
            self._devkits[name].queued_messages = queued_messages
            self._revision += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "revision": self._revision,
                "simulator_clients": self._simulator_clients,
                "monitor_clients": self._monitor_clients,
                "devkits": [asdict(devkit) for devkit in self._devkits.values()],
            }
