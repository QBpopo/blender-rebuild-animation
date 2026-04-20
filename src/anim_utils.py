from __future__ import annotations

import copy
import hashlib
import re
from typing import Any


def parse_bone_name(data_path: str) -> str | None:
    match = re.search(r'pose\.bones\["([^"]+)"\]', data_path)
    return match.group(1) if match else None


def fcurve_kind(data_path: str) -> str:
    if "rotation" in data_path:
        return "rotation"
    if "location" in data_path:
        return "location"
    if "scale" in data_path:
        return "scale"
    if '["' in data_path and '"]' in data_path:
        return "expression"
    return "other"


def close_enough(a: float, b: float, eps: float) -> bool:
    return abs(a - b) <= eps


def quantize_float(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def sequence_hash(frames: list[float], values: list[float], digits: int = 4) -> str:
    payload = "|".join(
        f"{quantize_float(f, digits)}:{quantize_float(v, digits)}"
        for f, v in zip(frames, values)
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def remove_animation_data_payload(blend_payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(blend_payload)
    cleaned["actions"] = []
    cleaned["armature_bindings"] = []
    cleaned["shape_key_bindings"] = []
    return cleaned
