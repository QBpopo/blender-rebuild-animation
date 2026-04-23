from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blender_common import blender_argv
from toml_io import load_toml


def _clear_all_animation() -> None:
    import bpy

    def clear_anim_data_keep_drivers(anim_data: Any) -> None:
        if not anim_data:
            return
        if hasattr(anim_data, "action_slot") and anim_data.action_slot is not None:
            anim_data.action_slot = None
        anim_data.action = None
        nla_tracks = getattr(anim_data, "nla_tracks", None)
        if nla_tracks is not None:
            for track in list(nla_tracks):
                nla_tracks.remove(track)

    for obj in bpy.data.objects:
        clear_anim_data_keep_drivers(obj.animation_data)

        data = getattr(obj, "data", None)
        if data and hasattr(data, "shape_keys") and data.shape_keys:
            clear_anim_data_keep_drivers(data.shape_keys.animation_data)

    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)


def _ensure_action(name: str):
    import bpy

    action = bpy.data.actions.get(name)
    if action:
        return action
    return bpy.data.actions.new(name=name)


def _find_bound_object_name(payload: dict[str, Any], action_name: str) -> str | None:
    for key in ("armature_bindings", "object_bindings"):
        for binding in payload.get(key, []):
            if binding.get("action") == action_name:
                return binding.get("object")
    for binding in payload.get("shape_key_bindings", []):
        if binding.get("action") == action_name:
            return binding.get("object")
    return None


def _resolve_datablock_for_fcurve(
    payload: dict[str, Any], action_name: str, data_path: str
):
    import bpy

    if data_path.startswith("key_blocks["):
        for binding in payload.get("shape_key_bindings", []):
            if binding.get("action") != action_name:
                continue
            obj = bpy.data.objects.get(binding.get("object"))
            if obj and getattr(obj, "data", None) and hasattr(obj.data, "shape_keys"):
                return obj.data.shape_keys
        return None

    obj_name = _find_bound_object_name(payload, action_name)
    if not obj_name:
        return None
    return bpy.data.objects.get(obj_name)


def _assign_actions_to_bindings(
    payload: dict[str, Any], action_index: dict[str, Any]
) -> None:
    import bpy

    for binding in payload.get("armature_bindings", []):
        obj = bpy.data.objects.get(binding.get("object"))
        action = action_index.get(binding.get("action"))
        if not obj or not action:
            continue
        if not obj.animation_data:
            obj.animation_data_create()
        obj.animation_data.action = action

    for binding in payload.get("object_bindings", []):
        obj = bpy.data.objects.get(binding.get("object"))
        action = action_index.get(binding.get("action"))
        if not obj or not action:
            continue
        if not obj.animation_data:
            obj.animation_data_create()
        obj.animation_data.action = action

    for binding in payload.get("shape_key_bindings", []):
        obj = bpy.data.objects.get(binding.get("object"))
        action = action_index.get(binding.get("action"))
        if not obj or not action:
            continue
        data = getattr(obj, "data", None)
        if not data or not hasattr(data, "shape_keys") or not data.shape_keys:
            continue
        if not data.shape_keys.animation_data:
            data.shape_keys.animation_data_create()
        data.shape_keys.animation_data.action = action


def _insert_keyframes(fcurve: Any, keyframes: list[dict[str, Any]]) -> None:
    for key in keyframes:
        point = fcurve.keyframe_points.insert(
            float(key["frame"]), float(key["value"]), options={"FAST"}
        )
        interp = str(key.get("interpolation", "BEZIER"))
        point.interpolation = interp
        if interp == "BEZIER":
            if "handle_left" in key and len(key["handle_left"]) == 2:
                point.handle_left = (
                    float(key["handle_left"][0]),
                    float(key["handle_left"][1]),
                )
            if "handle_right" in key and len(key["handle_right"]) == 2:
                point.handle_right = (
                    float(key["handle_right"][0]),
                    float(key["handle_right"][1]),
                )


def _generate_pattern_keyframes(pattern: dict[str, Any]) -> list[dict[str, Any]]:
    if pattern.get("algorithm") != "linear_ramp":
        return []

    frame_start = float(pattern["frame_start"])
    frame_step = float(pattern["frame_step"])
    count = int(pattern["count"])
    value_start = float(pattern["value_start"])
    value_step = float(pattern["value_step"])

    out: list[dict[str, Any]] = []
    for i in range(count):
        out.append(
            {
                "frame": frame_start + frame_step * i,
                "value": value_start + value_step * i,
                "interpolation": "LINEAR",
            }
        )
    return out


def rebuild_from_new_toml(
    new_toml: str, output_blend: str, clear_existing: bool = True
) -> None:
    import bpy

    payload = load_toml(new_toml)

    if clear_existing:
        _clear_all_animation()

    scene = payload.get("scene", {})
    if scene:
        bpy.context.scene.frame_start = int(
            scene.get("frame_start", bpy.context.scene.frame_start)
        )
        bpy.context.scene.frame_end = int(
            scene.get("frame_end", bpy.context.scene.frame_end)
        )

    action_index: dict[str, Any] = {}
    for action_data in payload.get("optimized_actions", []):
        action = _ensure_action(action_data["name"])
        if hasattr(action, "fcurves"):
            for fc in list(action.fcurves):
                action.fcurves.remove(fc)
        action.frame_range = (
            float(action_data.get("frame_range", [1.0, 1.0])[0]),
            float(action_data.get("frame_range", [1.0, 1.0])[1]),
        )
        action_index[action_data["name"]] = action

    _assign_actions_to_bindings(payload, action_index)

    for action_data in payload.get("optimized_actions", []):
        action = action_index[action_data["name"]]
        pattern_map: dict[tuple[str, int], dict[str, Any]] = {}
        for pattern in action_data.get("procedural_patterns", []):
            key = (str(pattern["target_data_path"]), int(pattern["target_array_index"]))
            pattern_map[key] = pattern

        for fc_data in action_data.get("fcurves", []):
            data_path = str(fc_data["data_path"])
            array_index = int(fc_data.get("array_index", 0))
            if hasattr(action, "fcurves"):
                fcurve = action.fcurves.new(data_path=data_path, index=array_index)
            else:
                datablock = _resolve_datablock_for_fcurve(
                    payload, action_data["name"], data_path
                )
                if datablock is None:
                    continue
                fcurve = action.fcurve_ensure_for_datablock(
                    datablock, data_path, index=array_index
                )
            fcurve.extrapolation = str(fc_data.get("extrapolation", "CONSTANT"))

            keyframes = fc_data.get("keyframes", [])
            if not keyframes:
                keyframes = _generate_pattern_keyframes(
                    pattern_map.get((data_path, array_index), {})
                )
            _insert_keyframes(fcurve, keyframes)
            fcurve.update()

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(output_blend))
    print(f"[engine.py] rebuilt: {output_blend}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild animation from optimized TOML"
    )
    parser.add_argument("--input", required=True, help="new.toml path")
    parser.add_argument("--output", required=True, help="output blend path")
    parser.add_argument(
        "--keep-existing", action="store_true", help="do not clear existing animation"
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args(blender_argv())
    rebuild_from_new_toml(
        args.input, args.output, clear_existing=not args.keep_existing
    )


if __name__ == "__main__":
    main()
