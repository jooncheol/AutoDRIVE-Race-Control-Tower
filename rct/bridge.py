# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import asyncio
import math
import re
import base64
import gzip
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
    "origin": 0
}
OUTGOING_BRIDGE_KEYS = frozenset(OUTGOING_BRIDGE_DEFAULTS)
BRIDGE_RATE_WINDOW_SECONDS = 1.0
VEHICLE_FIELD_PATTERN = re.compile(r"(?<![A-Za-z0-9])V(?P<vehicle_id>\d+)(?!\d)", re.IGNORECASE)
ROBORACER_FIELD_PATTERN = re.compile(r"roboracer_(?P<vehicle_id>\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class TimestampedBridgePayload:
    timestamp: float
    payload: Any
    payloads: dict[int, Any] = field(default_factory=dict)


class BridgeHistory:
    def __init__(self, retention_seconds: float) -> None:
        self.retention_seconds = retention_seconds
        self._records: deque[TimestampedBridgePayload] = deque()
        self._condition = asyncio.Condition()

    async def append(
        self,
        payload: Any,
        now: float | None = None,
        payloads: dict[int, Any] | None = None,
    ) -> TimestampedBridgePayload:
        timestamp = monotonic() if now is None else now
        record = TimestampedBridgePayload(
            timestamp=timestamp,
            payload=deepcopy(payload),
            payloads=deepcopy(payloads) if payloads is not None else {},
        )
        async with self._condition:
            self._prune_locked(timestamp)
            self._records.append(record)
            self._condition.notify_all()
        return self._copy_record(record)

    async def latest(self, now: float | None = None) -> TimestampedBridgePayload | None:
        async with self._condition:
            self._prune_locked(monotonic() if now is None else now)
            if not self._records:
                return None
            return self._copy_record(self._records[-1])

    async def oldest_after(
        self,
        after_timestamp: float,
        now: float | None = None,
    ) -> TimestampedBridgePayload | None:
        async with self._condition:
            self._prune_locked(monotonic() if now is None else now)
            for record in self._records:
                if record.timestamp > after_timestamp:
                    return self._copy_record(record)
        return None

    async def wait_for_oldest_after(self, after_timestamp: float) -> TimestampedBridgePayload:
        async with self._condition:
            while True:
                self._prune_locked(monotonic())
                for record in self._records:
                    if record.timestamp > after_timestamp:
                        return self._copy_record(record)
                await self._condition.wait()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.retention_seconds
        while self._records and self._records[0].timestamp < cutoff:
            self._records.popleft()

    def _copy_record(self, record: TimestampedBridgePayload) -> TimestampedBridgePayload:
        return TimestampedBridgePayload(
            record.timestamp,
            deepcopy(record.payload),
            deepcopy(record.payloads),
        )


class ControlCache:
    def __init__(self) -> None:
        self._payload = dict(OUTGOING_BRIDGE_DEFAULTS)
        self._timestamp = 0.0
        self._lock = asyncio.Lock()

    async def merge(
        self,
        payload: Any,
        timestamp: float,
        *,
        origin_vehicle_id: int | None = None,
        include_origin: bool = False,
    ) -> tuple[float, dict[str, Any]]:
        async with self._lock:
            if isinstance(payload, dict):
                for key, value in payload.items():
                    if key in OUTGOING_BRIDGE_KEYS:
                        self._payload[key] = value
            self._timestamp = max(self._timestamp, timestamp)
            self._payload["origin"] = origin_vehicle_id if include_origin and origin_vehicle_id is not None else 0
            snapshot = deepcopy(self._payload)
            snapshot_timestamp = self._timestamp
        if not include_origin:
            snapshot.pop("origin", None)
        return snapshot_timestamp, snapshot

    async def snapshot(self, *, include_origin: bool = False) -> tuple[float, dict[str, Any]]:
        async with self._lock:
            snapshot = deepcopy(self._payload)
            snapshot_timestamp = self._timestamp
        if not include_origin:
            snapshot.pop("origin", None)
        return snapshot_timestamp, snapshot


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
        elif field == "linear_velocity":
            vector = _vector3_value(value)
            if vector is None:
                continue
            vehicle_values[field] = vector
            horizontal_speed = math.hypot(vector["x"], vector["y"])
            if horizontal_speed > 0.01:
                vehicle_values["heading_yaw"] = math.atan2(vector["y"], vector["x"])
        elif field == "orientation_quaternion":
            quaternion = _quaternion_value(value)
            if quaternion is None:
                continue
            vehicle_values[field] = quaternion
            vehicle_values["heading_yaw"] = _yaw_from_quaternion(quaternion)
        else:
            vehicle_values[field] = value

    return telemetry


def extract_lidar_scans(
    payload: Any,
    vehicle_ids: set[int],
    vehicle_positions: dict[int, dict[str, Any]] | None = None,
) -> dict[int, list[dict[str, float]]]:
    if not isinstance(payload, dict) or not vehicle_ids:
        return {}

    scans: dict[int, list[dict[str, float]]] = {}
    topic_scan = _lidar_scan_from_topic_message(payload, vehicle_ids, vehicle_positions)
    if topic_scan is not None:
        vehicle_id, points = topic_scan
        scans[vehicle_id] = points
        return scans

    for key, value in payload.items():
        vehicle_id = _vehicle_id_from_key(key)
        if vehicle_id is None or vehicle_id not in vehicle_ids or not _is_lidar_scan_key(key):
            continue

        points = _lidar_points(
            value,
            _vehicle_origin(vehicle_id, vehicle_positions),
            ranges_are_distances=_is_lidar_range_key(key),
        )
        if points:
            scans[vehicle_id] = points

    return scans


def extract_lidar_range_arrays(payload: Any, vehicle_ids: set[int]) -> dict[int, Any]:
    if not isinstance(payload, dict) or not vehicle_ids:
        return {}

    arrays: dict[int, Any] = {}
    for key, value in payload.items():
        vehicle_id = _vehicle_id_from_key(key)
        if vehicle_id is None or vehicle_id not in vehicle_ids:
            continue
        if _is_lidar_range_array_key(key):
            arrays[vehicle_id] = _lidar_range_values(value)
    return arrays


def _lidar_scan_from_topic_message(
    payload: dict[Any, Any],
    vehicle_ids: set[int],
    vehicle_positions: dict[int, dict[str, Any]] | None,
) -> tuple[int, list[dict[str, float]]] | None:
    topic = payload.get("topic", payload.get("path"))
    if topic is None:
        return None

    vehicle_id = _vehicle_id_from_key(topic)
    if vehicle_id is None or vehicle_id not in vehicle_ids or not _is_lidar_scan_key(topic):
        return None

    value = payload.get("payload", payload.get("data", payload.get("value")))
    points = _lidar_points(
        value,
        _vehicle_origin(vehicle_id, vehicle_positions),
        ranges_are_distances=_is_lidar_range_key(topic),
    )
    if not points:
        return None
    return vehicle_id, points


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


def _is_lidar_scan_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return "lidar" in normalized


def _is_lidar_range_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return "lidar" in normalized and "range" in normalized


def _is_lidar_range_array_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return "lidar" in normalized and "range" in normalized and "array" in normalized


def _vehicle_origin(
    vehicle_id: int,
    vehicle_positions: dict[int, dict[str, Any]] | None,
) -> tuple[float, float] | None:
    if vehicle_positions is None:
        return None
    position = vehicle_positions.get(vehicle_id, {}).get("ips")
    if not isinstance(position, dict):
        return None
    x = _numeric_float(position.get("x"))
    y = _numeric_float(position.get("y"))
    if x is None or y is None:
        return None
    return x, y


def _lidar_points(
    value: Any,
    origin: tuple[float, float] | None = None,
    ranges_are_distances: bool = False,
) -> list[dict[str, float]]:
    if isinstance(value, dict):
        if "points" in value:
            return _lidar_points(value["points"], origin, ranges_are_distances=False)
        if "scan" in value:
            return _lidar_points(value["scan"], origin, ranges_are_distances=ranges_are_distances)
        if "ranges" in value:
            return _lidar_points(value["ranges"], origin, ranges_are_distances=True)

        x_values = value.get("x", value.get("X"))
        y_values = value.get("y", value.get("Y"))
        if isinstance(x_values, list) and isinstance(y_values, list):
            points = []
            for x_value, y_value in zip(x_values, y_values):
                point = _lidar_point_from_xy(x_value, y_value)
                if point is not None:
                    points.append(point)
            return points

        point = _lidar_point_from_xy(value.get("x", value.get("X")), value.get("y", value.get("Y")))
        return [point] if point is not None else []

    if isinstance(value, str):
        value = _numeric_items_from_text(value)

    if not isinstance(value, (list, tuple)):
        return []

    points = []
    if value and all(isinstance(item, dict) for item in value):
        for item in value:
            point = _lidar_point_from_xy(item.get("x", item.get("X")), item.get("y", item.get("Y")))
            if point is not None:
                points.append(point)
        return points

    if value and all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in value):
        for item in value:
            point = _lidar_point_from_xy(item[0], item[1])
            if point is not None:
                points.append(point)
        return points

    numeric_values = [_numeric_float(item) for item in value]
    if any(item is None for item in numeric_values):
        return []

    numbers = [float(item) for item in numeric_values if item is not None]
    if len(numbers) >= 2 and len(numbers) % 2 == 0 and not ranges_are_distances:
        for index in range(0, len(numbers), 2):
            points.append({"x": numbers[index], "y": numbers[index + 1]})
        return points

    if origin is None or not ranges_are_distances:
        return []

    origin_x, origin_y = origin
    if not numbers:
        return []

    angle_min = -135.0
    angle_span = 270.0
    denominator = max(1, len(numbers) - 1)
    for index, distance in enumerate(numbers):
        radians = math.radians(angle_min + angle_span * index / denominator)
        points.append(
            {
                "x": origin_x + distance * math.cos(radians),
                "y": origin_y + distance * math.sin(radians),
            }
        )
    return points


def _lidar_point_from_xy(x_value: Any, y_value: Any) -> dict[str, float] | None:
    x = _numeric_float(x_value)
    y = _numeric_float(y_value)
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _numeric_items_from_text(value: str) -> list[str]:
    return re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value)


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
    if "collision" in normalized:
        return "collision_count"
    if "last" in normalized and "lap" in normalized and ("count" in normalized or "time" in normalized):
        return "last_lap_time"
    if "lap" in normalized and "time" in normalized:
        return "lap_time"
    if "lap" in normalized and "count" in normalized:
        return "lap_count"
    if "speed" in normalized:
        return "speed"
    if "linear" in normalized and "velocity" in normalized:
        return "linear_velocity"
    if "orientation" in normalized and "quaternion" in normalized:
        return "orientation_quaternion"
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


def _vector3_value(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        x = _numeric_float(value.get("x", value.get("X")))
        y = _numeric_float(value.get("y", value.get("Y")))
        z = _numeric_float(value.get("z", value.get("Z")))
    elif isinstance(value, (list, tuple)):
        if len(value) < 2:
            return None
        x = _numeric_float(value[0])
        y = _numeric_float(value[1])
        z = _numeric_float(value[2]) if len(value) > 2 else 0.0
    elif isinstance(value, str):
        numbers = [_numeric_float(item) for item in _numeric_items_from_text(value)]
        if len(numbers) < 2:
            return None
        x = numbers[0]
        y = numbers[1]
        z = numbers[2] if len(numbers) > 2 else 0.0
    else:
        return None

    if x is None or y is None:
        return None
    return {"x": x, "y": y, "z": z if z is not None else 0.0}


def _quaternion_value(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        x = _numeric_float(value.get("x", value.get("X")))
        y = _numeric_float(value.get("y", value.get("Y")))
        z = _numeric_float(value.get("z", value.get("Z")))
        w = _numeric_float(value.get("w", value.get("W")))
    elif isinstance(value, (list, tuple)):
        if len(value) < 4:
            return None
        x = _numeric_float(value[0])
        y = _numeric_float(value[1])
        z = _numeric_float(value[2])
        w = _numeric_float(value[3])
    elif isinstance(value, str):
        numbers = [_numeric_float(item) for item in _numeric_items_from_text(value)]
        if len(numbers) < 4:
            return None
        x, y, z, w = numbers[:4]
    else:
        return None

    if x is None or y is None or z is None or w is None:
        return None
    return {"x": x, "y": y, "z": z, "w": w}


def _yaw_from_quaternion(quaternion: dict[str, float]) -> float:
    x = quaternion["x"]
    y = quaternion["y"]
    z = quaternion["z"]
    w = quaternion["w"]
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _lidar_range_values(value: Any) -> list[float]:
    if isinstance(value, str):
        try:
            decoded = gzip.decompress(base64.b64decode(value)).decode("utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            decoded = value
        return [float(item) for item in _numeric_items_from_text(decoded)]

    if isinstance(value, (list, tuple)):
        numbers = [_numeric_float(item) for item in value]
        if all(item is not None for item in numbers):
            return [float(item) for item in numbers if item is not None]

    return []


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
