# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import re
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

OUTGOING_BRIDGE_DEFAULTS: dict[str, str] = {
    "V1 Reset": "False",
    "V1 Throttle": "0.0",
    "V1 Steering": "0.0",
    "V2 Reset": "False",
    "V2 Throttle": "0.0",
    "V2 Steering": "0.0",
}
OUTGOING_BRIDGE_KEYS = frozenset(OUTGOING_BRIDGE_DEFAULTS)
BRIDGE_RATE_WINDOW_SECONDS = 60.0
VEHICLE_FIELD_PATTERN = re.compile(r"(?<![A-Za-z0-9])V(?P<vehicle_id>\d+)(?!\d)", re.IGNORECASE)
ROBORACER_FIELD_PATTERN = re.compile(r"roboracer_(?P<vehicle_id>\d+)", re.IGNORECASE)


@dataclass
class BridgeCache:
    pending_limit: int = 1024
    incoming_cache: dict[str, Any] = field(default_factory=dict)
    outgoing_cache: dict[str, Any] = field(default_factory=lambda: dict(OUTGOING_BRIDGE_DEFAULTS))
    _inflight_vehicle_ids: set[int] = field(init=False)
    _dirty_vehicle_ids: set[int] = field(init=False)

    def __post_init__(self) -> None:
        self._inflight_vehicle_ids = set()
        self._dirty_vehicle_ids = set()

    def update_incoming(self, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        self.incoming_cache = deepcopy(payload)
        return deepcopy(self.incoming_cache)

    def current_incoming(self) -> dict[str, Any] | None:
        if not self.incoming_cache:
            return None
        return deepcopy(self.incoming_cache)

    def update_outgoing(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in OUTGOING_BRIDGE_KEYS:
                    self.outgoing_cache[key] = value

        return deepcopy(self.outgoing_cache)

    def current_outgoing(self) -> dict[str, Any]:
        return deepcopy(self.outgoing_cache)

    def request_outgoing(self, vehicle_id: int) -> bool:
        if self._inflight_vehicle_ids:
            self._dirty_vehicle_ids.add(vehicle_id)
            return False

        self._inflight_vehicle_ids = {vehicle_id}
        return True

    def complete_inflight(self) -> set[int] | None:
        if not self._inflight_vehicle_ids:
            return None

        vehicle_ids = set(self._inflight_vehicle_ids)
        self._inflight_vehicle_ids.clear()
        return vehicle_ids

    def start_queued_outgoing(self) -> set[int]:
        if self._inflight_vehicle_ids or not self._dirty_vehicle_ids:
            return set()

        vehicle_ids = set(self._dirty_vehicle_ids)
        self._dirty_vehicle_ids.clear()
        self._inflight_vehicle_ids = set(vehicle_ids)
        return vehicle_ids

    @property
    def pending_response_count(self) -> int:
        return len(self._inflight_vehicle_ids)

    @property
    def queued_outgoing_count(self) -> int:
        return len(self._dirty_vehicle_ids)


class BridgeRateTracker:
    def __init__(self, window_seconds: float = BRIDGE_RATE_WINDOW_SECONDS) -> None:
        self.window_seconds = window_seconds
        self._timestamps: dict[int, deque[float]] = {}

    def record(self, vehicle_id: int, now: float | None = None) -> dict[str, float | int]:
        now = monotonic() if now is None else now
        timestamps = self._timestamps.setdefault(vehicle_id, deque())
        timestamps.append(now)
        self._prune(timestamps, now)
        return self.rates(vehicle_id, now)

    def rates(self, vehicle_id: int, now: float | None = None) -> dict[str, float | int]:
        now = monotonic() if now is None else now
        completed_cycles = self._active_count(vehicle_id, now)
        return {
            "bridge_hz": completed_cycles / self.window_seconds,
            "bridge_per_minute": round(completed_cycles * 60.0 / self.window_seconds),
        }

    def _active_count(self, vehicle_id: int, now: float) -> int:
        timestamps = self._timestamps.setdefault(vehicle_id, deque())
        self._prune(timestamps, now)
        return len(timestamps)

    def _prune(self, timestamps: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()


def extract_collision_counts(payload: Any) -> dict[int, int]:
    if not isinstance(payload, dict):
        return {}

    counts: dict[int, int] = {}
    for key, value in payload.items():
        key_text = str(key)
        if "collision" not in key_text.lower():
            continue

        match = VEHICLE_FIELD_PATTERN.search(key_text)
        if match is None:
            continue

        count = _numeric_count(value)
        if count is None:
            continue

        vehicle_id = int(match.group("vehicle_id"))
        counts[vehicle_id] = max(counts.get(vehicle_id, count), count)
    return counts


def extract_monitor_telemetry(payload: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}

    telemetry: dict[int, dict[str, Any]] = {}
    topic_telemetry = _monitor_telemetry_from_topic_message(payload)
    if topic_telemetry is not None:
        vehicle_id, field, value = topic_telemetry
        telemetry.setdefault(vehicle_id, {})[field] = value
        return telemetry

    for key, value in payload.items():
        vehicle_id = _vehicle_id_from_key(key)
        if vehicle_id is None:
            continue

        field = _monitor_field_from_key(key)
        if field is None:
            continue

        vehicle_values = telemetry.setdefault(vehicle_id, {})
        if field == "ips":
            ips = _ips_value(value)
            if ips is None:
                continue
            vehicle_values[field] = ips
        elif field in {"collision_count", "lap_count"}:
            count = _numeric_count(value)
            if count is None:
                continue
            vehicle_values[field] = count
        elif field == "speed":
            speed = _numeric_float(value)
            vehicle_values[field] = speed if speed is not None else value
        else:
            vehicle_values[field] = value

    return telemetry


def _monitor_telemetry_from_topic_message(payload: dict[Any, Any]) -> tuple[int, str, Any] | None:
    topic = payload.get("topic", payload.get("path"))
    if topic is None:
        return None

    vehicle_id = _vehicle_id_from_key(topic)
    field = _monitor_field_from_key(topic)
    if vehicle_id is None or field is None:
        return None

    value = payload.get("payload", payload.get("data", payload.get("value")))
    if field == "ips":
        value = _ips_value(value)
        if value is None:
            return None
    elif field in {"collision_count", "lap_count"}:
        value = _numeric_count(value)
        if value is None:
            return None
    elif field == "speed":
        numeric_value = _numeric_float(value)
        value = numeric_value if numeric_value is not None else value

    return vehicle_id, field, value


def _vehicle_id_from_key(key: Any) -> int | None:
    key_text = str(key)
    match = VEHICLE_FIELD_PATTERN.search(key_text) or ROBORACER_FIELD_PATTERN.search(key_text)
    if match is None:
        return None
    return int(match.group("vehicle_id"))


def _monitor_field_from_key(key: Any) -> str | None:
    key_text = str(key).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", key_text).strip("_")

    if "best" in normalized and "lap" in normalized and "time" in normalized:
        return "best_lap_time"
    if "collision" in normalized and "count" in normalized:
        return "collision_count"
    if "last" in normalized and "lap" in normalized and ("count" in normalized or "time" in normalized):
        return "last_lap_count"
    if "lap" in normalized and "count" in normalized:
        return "lap_count"
    if "speed" in normalized:
        return "speed"
    if "ips" in normalized or "position" in normalized:
        return "ips"
    return None


def _numeric_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _ips_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        x = _numeric_float(value.get("x", value.get("X")))
        y = _numeric_float(value.get("y", value.get("Y")))
        z = _numeric_float(value.get("z", value.get("Z")))
        if x is None or y is None:
            return None
        ips: dict[str, Any] = {"x": x, "y": y}
        if z is not None:
            ips["z"] = z
        ips["raw"] = deepcopy(value)
        return ips

    if isinstance(value, (list, tuple)):
        numbers = [_numeric_float(item) for item in value[:3]]
    elif isinstance(value, str):
        numbers = [_numeric_float(item) for item in re.split(r"[\s,]+", value.strip()) if item]
    else:
        return None

    if len(numbers) < 2 or numbers[0] is None or numbers[1] is None:
        return None

    ips = {"x": numbers[0], "y": numbers[1]}
    if len(numbers) > 2 and numbers[2] is not None:
        ips["z"] = numbers[2]
    ips["raw"] = value
    return ips


def _numeric_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None
