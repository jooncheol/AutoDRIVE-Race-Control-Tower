# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


MONITOR_PROTOCOL_VERSION = "0.1"
MONITOR_PROTOCOL_LATEST = "latest"
MONITOR_REST_TRANSPORT = "REST"
MONITOR_WS_TRANSPORT = "WS"


@dataclass(frozen=True)
class MonitorPath:
    transport: str
    requested_version: str
    resolved_version: str


def parse_monitor_path(request_path: str) -> MonitorPath | None:
    parsed = urlparse(request_path)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 3 or path_parts[0] != "monitor":
        return None

    transport = path_parts[1]
    requested_version = path_parts[2]
    if transport not in {MONITOR_REST_TRANSPORT, MONITOR_WS_TRANSPORT}:
        return None

    if requested_version == MONITOR_PROTOCOL_LATEST:
        resolved_version = MONITOR_PROTOCOL_VERSION
    elif requested_version == MONITOR_PROTOCOL_VERSION:
        resolved_version = requested_version
    else:
        return None

    return MonitorPath(
        transport=transport,
        requested_version=requested_version,
        resolved_version=resolved_version,
    )


def is_monitor_rest_path(request_path: str) -> bool:
    monitor_path = parse_monitor_path(request_path)
    return monitor_path is not None and monitor_path.transport == MONITOR_REST_TRANSPORT


def is_monitor_ws_path(request_path: str) -> bool:
    monitor_path = parse_monitor_path(request_path)
    return monitor_path is not None and monitor_path.transport == MONITOR_WS_TRANSPORT
