# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc


def _get_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw_value!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    devkit_urls: tuple[str, ...]
    devkit_vehicle_ids: tuple[int, ...]
    bridge_history_seconds: float
    enable_presplit_bridge_cache: bool
    log_bridge_field_sizes: bool
    empty_front_camera_in_bridge_history: bool
    replace_front_camera_with_white_jpeg: bool
    reconnect_delay_seconds: float
    max_message_size: int | None
    client_queue_size: int
    ping_interval_seconds: int
    ping_timeout_seconds: int
    monitor_ws_hz: float
    debug_engineio_messages: bool
    debug_engineio_max_chars: int
    debug_socketio_client: bool
    debug_engineio_client: bool
    debug_socketio_server: bool
    debug_engineio_server: bool
    debug_bridge_flow: bool
    log_bridge_messages: bool
    log_bridge_max_chars: int
    enable_origin: bool


def load_settings() -> Settings:
    devkit_urls = tuple(
        url.strip()
        for url in os.getenv(
            "RCT_DEVKIT_URLS",
            "ws://127.0.0.1:4568,ws://127.0.0.1:4569",
        ).split(",")
        if url.strip()
    )
    vehicle_ids_raw = os.getenv("RCT_DEVKIT_VEHICLE_IDS")
    if vehicle_ids_raw:
        devkit_vehicle_ids = tuple(int(value.strip()) for value in vehicle_ids_raw.split(",") if value.strip())
    else:
        devkit_vehicle_ids = tuple(range(1, len(devkit_urls) + 1))

    if len(devkit_vehicle_ids) != len(devkit_urls):
        raise ValueError("RCT_DEVKIT_VEHICLE_IDS must contain one id for each RCT_DEVKIT_URLS entry")

    max_message_size = _get_int("RCT_MAX_MESSAGE_SIZE", 16 * 1024 * 1024)
    if max_message_size <= 0:
        max_message_size = None

    return Settings(
        host=os.getenv("RCT_HOST", "0.0.0.0"),
        port=_get_int("RCT_PORT", 4567),
        devkit_urls=devkit_urls,
        devkit_vehicle_ids=devkit_vehicle_ids,
        bridge_history_seconds=_get_float("RCT_BRIDGE_HISTORY_SECONDS", 5.0),
        enable_presplit_bridge_cache=_get_bool("RCT_ENABLE_PRESPLIT_BRIDGE_CACHE", True),
        log_bridge_field_sizes=_get_bool("RCT_LOG_BRIDGE_FIELD_SIZES", False),
        empty_front_camera_in_bridge_history=_get_bool("RCT_EMPTY_FRONT_CAMERA_IN_BRIDGE_HISTORY", False),
        replace_front_camera_with_white_jpeg=_get_bool("RCT_REPLACE_FRONT_CAMERA_WITH_WHITE_JPEG", True),
        reconnect_delay_seconds=_get_float("RCT_RECONNECT_DELAY_SECONDS", 3.0),
        max_message_size=max_message_size,
        client_queue_size=_get_int("RCT_CLIENT_QUEUE_SIZE", 256),
        ping_interval_seconds=_get_int("RCT_PING_INTERVAL_SECONDS", 20),
        ping_timeout_seconds=_get_int("RCT_PING_TIMEOUT_SECONDS", 20),
        monitor_ws_hz=_get_float("RCT_MONITOR_WS_HZ", 0.0),
        debug_engineio_messages=_get_bool("RCT_DEBUG_ENGINEIO_MESSAGES", False),
        debug_engineio_max_chars=_get_int("RCT_DEBUG_ENGINEIO_MAX_CHARS", 2000),
        debug_socketio_client=_get_bool("RCT_DEBUG_SOCKETIO_CLIENT", False),
        debug_engineio_client=_get_bool("RCT_DEBUG_ENGINEIO_CLIENT", False),
        debug_socketio_server=_get_bool("RCT_DEBUG_SOCKETIO_SERVER", False),
        debug_engineio_server=_get_bool("RCT_DEBUG_ENGINEIO_SERVER", False),
        debug_bridge_flow=_get_bool("RCT_DEBUG_BRIDGE_FLOW", False),
        log_bridge_messages=_get_bool("RCT_LOG_BRIDGE_MESSAGES", False),
        log_bridge_max_chars=_get_int("RCT_LOG_BRIDGE_MAX_CHARS", 20000),
        enable_origin=_get_bool("RCT_ENABLE_ORIGIN", False),
    )
