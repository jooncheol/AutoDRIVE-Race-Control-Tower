from __future__ import annotations

import json
import re
from typing import Any

ROBORACER_ID_PATTERN = re.compile(r"roboracer_(\d+)")
AUTODRIVE_FIELD_ID_PATTERN = re.compile(r"\bV(\d+)(?=\s)")


class _DropValue:
    pass


DROP_VALUE = _DropValue()


def vehicle_ids_in_text(value: str) -> set[int]:
    ids = {int(match.group(1)) for match in ROBORACER_ID_PATTERN.finditer(value)}
    ids.update(int(match.group(1)) for match in AUTODRIVE_FIELD_ID_PATTERN.finditer(value))
    return ids


def rewrite_text_vehicle_id(value: str, source_id: int, target_id: int) -> str:
    value = re.sub(rf"roboracer_{source_id}\b", f"roboracer_{target_id}", value)
    value = re.sub(rf"\bV{source_id}(?=\s)", f"V{target_id}", value)
    return value


def rewrite_simulator_to_devkit(message: str | bytes, vehicle_id: int) -> str | bytes | None:
    return rewrite_message_vehicle_id(
        message,
        source_id=vehicle_id,
        target_id=1,
        drop_other_vehicle_data=True,
    )


def rewrite_devkit_to_simulator(message: str | bytes, vehicle_id: int) -> str | bytes:
    rewritten = rewrite_message_vehicle_id(
        message,
        source_id=1,
        target_id=vehicle_id,
        drop_other_vehicle_data=False,
    )
    return message if rewritten is None else rewritten


def rewrite_message_vehicle_id(
    message: str | bytes,
    source_id: int,
    target_id: int,
    drop_other_vehicle_data: bool,
) -> str | bytes | None:
    if isinstance(message, bytes):
        return message

    try:
        parsed_message = json.loads(message)
    except json.JSONDecodeError:
        ids = vehicle_ids_in_text(message)
        if drop_other_vehicle_data and ids and source_id not in ids:
            return None
        return rewrite_text_vehicle_id(message, source_id, target_id)

    rewritten = _rewrite_json_value(
        parsed_message,
        source_id=source_id,
        target_id=target_id,
        drop_other_vehicle_data=drop_other_vehicle_data,
    )
    if rewritten is DROP_VALUE:
        return None
    return json.dumps(rewritten, separators=(",", ":"))


def _rewrite_json_value(
    value: Any,
    source_id: int,
    target_id: int,
    drop_other_vehicle_data: bool,
) -> Any:
    if isinstance(value, dict):
        if drop_other_vehicle_data and _dict_points_to_other_vehicle(value, source_id):
            return DROP_VALUE

        rewritten: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_ids = vehicle_ids_in_text(key_text)
            if drop_other_vehicle_data and key_ids and source_id not in key_ids:
                continue

            rewritten_item = _rewrite_json_value(
                item,
                source_id=source_id,
                target_id=target_id,
                drop_other_vehicle_data=drop_other_vehicle_data,
            )
            if rewritten_item is DROP_VALUE:
                continue

            rewritten_key = (
                rewrite_text_vehicle_id(key, source_id, target_id)
                if isinstance(key, str)
                else key
            )
            rewritten[rewritten_key] = rewritten_item

        if drop_other_vehicle_data and not rewritten and _json_contains_vehicle_id(value):
            return DROP_VALUE
        return rewritten

    if isinstance(value, list):
        rewritten_items = [
            _rewrite_json_value(
                item,
                source_id=source_id,
                target_id=target_id,
                drop_other_vehicle_data=drop_other_vehicle_data,
            )
            for item in value
        ]
        rewritten_list = [item for item in rewritten_items if item is not DROP_VALUE]
        if drop_other_vehicle_data and not rewritten_list and _json_contains_vehicle_id(value):
            return DROP_VALUE
        return rewritten_list

    if isinstance(value, str):
        ids = vehicle_ids_in_text(value)
        if drop_other_vehicle_data and ids and source_id not in ids:
            return DROP_VALUE
        return rewrite_text_vehicle_id(value, source_id, target_id)

    return value


def _dict_points_to_other_vehicle(value: dict[Any, Any], source_id: int) -> bool:
    for key in ("topic", "path", "frame_id", "child_frame_id", "name"):
        item = value.get(key)
        if isinstance(item, str):
            ids = vehicle_ids_in_text(item)
            if ids and source_id not in ids:
                return True
    return False


def _json_contains_vehicle_id(value: Any) -> bool:
    if isinstance(value, dict):
        return any(vehicle_ids_in_text(str(key)) for key in value) or any(
            _json_contains_vehicle_id(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_json_contains_vehicle_id(item) for item in value)
    if isinstance(value, str):
        return bool(vehicle_ids_in_text(value))
    return False
