from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blender_common import blender_argv
from toml_io import dump_toml


def _extract_keyframe(point: Any) -> dict[str, Any]:
    return {
        "frame": float(point.co[0]),
        "value": float(point.co[1]),
        "handle_left": [float(point.handle_left[0]), float(point.handle_left[1])],
        "handle_right": [float(point.handle_right[0]), float(point.handle_right[1])],
        "interpolation": str(point.interpolation),
        "easing": str(getattr(point, "easing", "AUTO")),
        "key_type": str(getattr(point, "type", "KEYFRAME")),
    }


def _extract_fcurve(curve: Any) -> dict[str, Any]:
    return {
        "data_path": str(curve.data_path),
        "array_index": int(curve.array_index),
        "extrapolation": str(curve.extrapolation),
        "keyframes": [_extract_keyframe(p) for p in curve.keyframe_points],
    }


def _iter_action_fcurves(action: Any) -> list[Any]:
    if hasattr(action, "fcurves"):
        return list(action.fcurves)

    curves: list[Any] = []
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for channelbag in getattr(strip, "channelbags", []):
                curves.extend(list(getattr(channelbag, "fcurves", [])))
    return curves


def export_animation_to_toml(output_path: str) -> None:
    import bpy

    scene = bpy.context.scene
    payload: dict[str, Any] = {
        "meta": {
            "format": "raw_animation_toml",
            "version": "1.0",
        },
        "scene": {
            "name": scene.name,
            "frame_start": int(scene.frame_start),
            "frame_end": int(scene.frame_end),
            "fps": float(scene.render.fps),
        },
        "armature_bindings": [],
        "object_bindings": [],
        "shape_key_bindings": [],
        "actions": [],
    }

    for obj in bpy.data.objects:
        if not obj.animation_data or not obj.animation_data.action:
            continue
        binding = {
            "object": obj.name,
            "object_type": obj.type,
            "action": obj.animation_data.action.name,
        }
        if obj.type == "ARMATURE":
            payload["armature_bindings"].append(binding)
        else:
            payload["object_bindings"].append(binding)

    for obj in bpy.data.objects:
        if not obj.data or not hasattr(obj.data, "shape_keys"):
            continue
        shape_keys = obj.data.shape_keys
        if (
            shape_keys
            and shape_keys.animation_data
            and shape_keys.animation_data.action
        ):
            payload["shape_key_bindings"].append(
                {
                    "object": obj.name,
                    "shape_keys": shape_keys.name,
                    "action": shape_keys.animation_data.action.name,
                }
            )

    for action in bpy.data.actions:
        fcurves = _iter_action_fcurves(action)
        action_item = {
            "name": action.name,
            "frame_range": [float(action.frame_range[0]), float(action.frame_range[1])],
            "fcurves": [_extract_fcurve(fc) for fc in fcurves],
        }
        payload["actions"].append(action_item)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    dump_toml(payload, out)
    print(f"[old.py] exported: {out}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export raw animation data from blend to TOML"
    )
    parser.add_argument("--output", required=True, help="output old.toml path")
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args(blender_argv())
    export_animation_to_toml(os.path.abspath(args.output))


if __name__ == "__main__":
    main()
