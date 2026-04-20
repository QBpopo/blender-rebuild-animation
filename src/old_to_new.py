from __future__ import annotations

import argparse
import copy
import fnmatch
import math
from pathlib import Path
from typing import Any

from anim_utils import close_enough, fcurve_kind, parse_bone_name
from toml_io import dump_toml, load_toml


DEFAULT_OPT = {
    "enable_duplicate_keyframe_prune": True,
    "duplicate_epsilon_frame": 1e-6,
    "duplicate_epsilon_value": 1e-6,
    "enable_bezier_to_linear": True,
    "bezier_to_linear_threshold": 0.01,
    "enable_interval_dedupe": True,
    "rotation_threshold_deg": 1.0,
    "location_threshold_px": 0.5,
    "interval_dedupe_value_threshold": 1e-4,
    "dedupe_interval_sec": 0.2,
    "enable_minor_keyframe_prune": True,
    "minor_prune_allow_interpolations": ["LINEAR"],
    "minor_prune_neighbor_interpolations": ["LINEAR", "CONSTANT"],
    "enable_rdp_simplify": True,
    "rdp_epsilon": 0.001,
    "scale_threshold": 0.01,
    "expression_threshold": 0.001,
    "other_threshold": 0.001,
    "min_keyframes_per_fcurve": 1,
    "enable_linear_pattern_detection": True,
    "linear_pattern_min_points": 3,
    "linear_pattern_frame_epsilon": 1e-5,
    "linear_pattern_value_epsilon": 1e-5,
    "keep_procedural_source_keyframes": False,
    "prune_exclude_data_path_keywords": [],
    "pattern_exclude_data_path_keywords": [],
    "action_include_patterns": [],
    "action_exclude_patterns": [],
    "keep_empty_actions": False,
    "omit_default_interpolation": True,
    "omit_handles_for_linear_constant": True,
    "omit_kind_field": True,
    "omit_default_extrapolation": True,
}


def _threshold_for_kind(kind: str, opt: dict[str, Any]) -> float:
    if kind == "rotation":
        return math.radians(float(opt["rotation_threshold_deg"]))
    if kind == "location":
        return float(opt["location_threshold_px"])
    if kind == "scale":
        return float(opt["scale_threshold"])
    if kind == "expression":
        return float(opt["expression_threshold"])
    return float(opt["other_threshold"])


def _bezier_point_on_segment(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    u = 1.0 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def _simplify_bezier_to_linear(
    keyframes: list[dict[str, Any]], threshold: float
) -> list[dict[str, Any]]:
    if len(keyframes) <= 2 or threshold <= 0:
        return keyframes

    result = [keyframes[0]]
    for i in range(1, len(keyframes) - 1):
        kf = keyframes[i]
        if kf.get("interpolation") != "BEZIER":
            result.append(kf)
            continue

        prev = result[-1]
        next_kf = keyframes[i + 1]

        x0, y0 = float(prev["frame"]), float(prev["value"])
        x3, y3 = float(next_kf["frame"]), float(next_kf["value"])
        x, y = float(kf["frame"]), float(kf["value"])

        if close_enough(x0, x3, 1e-12):
            result.append(kf)
            continue

        linear_y = y0 + (y3 - y0) * ((x - x0) / (x3 - x0))

        if abs(linear_y - y) > threshold:
            result.append(kf)
            continue

        hl = kf.get("handle_left", [x, y])
        hr = kf.get("handle_right", [x, y])
        p0 = (x0, y0)
        p1 = (float(hl[0]), float(hl[1]))
        p2 = (float(hr[0]), float(hr[1]))
        p3 = (x3, y3)

        max_dev = 0.0
        for s in range(1, 10):
            t = s / 10.0
            bx, by = _bezier_point_on_segment(p0, p1, p2, p3, t)
            lin_y = y0 + (y3 - y0) * ((bx - x0) / (x3 - x0))
            dev = abs(by - lin_y)
            if dev > max_dev:
                max_dev = dev

        if max_dev <= threshold:
            new_kf = {
                "frame": kf["frame"],
                "value": kf["value"],
                "interpolation": "LINEAR",
            }
            result.append(new_kf)
        else:
            result.append(kf)

    result.append(keyframes[-1])
    return result


def _rdp_find_farthest(
    points: list[tuple[float, float]], start: int, end: int
) -> tuple[int, float]:
    x0, y0 = points[start]
    x1, y1 = points[end]
    dx = x1 - x0
    dy = y1 - y0
    line_len_sq = dx * dx + dy * dy

    max_dist = 0.0
    max_idx = start

    for i in range(start + 1, end):
        if line_len_sq < 1e-24:
            dist = math.hypot(points[i][0] - x0, points[i][1] - y0)
        else:
            t = ((points[i][0] - x0) * dx + (points[i][1] - y0) * dy) / line_len_sq
            t = max(0.0, min(1.0, t))
            proj_x = x0 + t * dx
            proj_y = y0 + t * dy
            dist = math.hypot(points[i][0] - proj_x, points[i][1] - proj_y)
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    return max_idx, max_dist


def _rdp_simplify_indices(
    points: list[tuple[float, float]], start: int, end: int, epsilon: float
) -> set[int]:
    if end - start <= 1:
        return {start, end}

    far_idx, far_dist = _rdp_find_farthest(points, start, end)

    if far_dist <= epsilon:
        return {start, end}

    left = _rdp_simplify_indices(points, start, far_idx, epsilon)
    right = _rdp_simplify_indices(points, far_idx, end, epsilon)
    return left | right


def _rdp_simplify(
    keyframes: list[dict[str, Any]], epsilon: float
) -> list[dict[str, Any]]:
    if len(keyframes) <= 2 or epsilon <= 0:
        return keyframes

    points = [(float(kf["frame"]), float(kf["value"])) for kf in keyframes]
    keep_indices = _rdp_simplify_indices(points, 0, len(points) - 1, epsilon)
    return [keyframes[i] for i in sorted(keep_indices)]


def _remove_minor_keyframes(
    keyframes: list[dict[str, Any]], threshold: float
) -> list[dict[str, Any]]:
    if len(keyframes) <= 2:
        return keyframes

    result = [keyframes[0]]
    for i in range(1, len(keyframes) - 1):
        prev_kf = result[-1]
        curr_kf = keyframes[i]
        next_kf = keyframes[i + 1]

        if curr_kf.get("interpolation") != "LINEAR":
            result.append(curr_kf)
            continue
        if prev_kf.get("interpolation") not in {"LINEAR", "CONSTANT"}:
            result.append(curr_kf)
            continue
        if next_kf.get("interpolation") not in {"LINEAR", "CONSTANT"}:
            result.append(curr_kf)
            continue

        x1, y1 = prev_kf["frame"], prev_kf["value"]
        x2, y2 = next_kf["frame"], next_kf["value"]
        x = curr_kf["frame"]

        if close_enough(x1, x2, 1e-12):
            result.append(curr_kf)
            continue

        if prev_kf.get("interpolation") == "CONSTANT":
            predicted = y1
        else:
            predicted = y1 + (y2 - y1) * ((x - x1) / (x2 - x1))
        if abs(predicted - curr_kf["value"]) > threshold:
            result.append(curr_kf)

    result.append(keyframes[-1])
    return result


def _remove_dense_redundant_keyframes(
    keyframes: list[dict[str, Any]],
    min_frame_interval: float,
    value_threshold: float,
) -> list[dict[str, Any]]:
    if len(keyframes) <= 2 or min_frame_interval <= 0:
        return keyframes

    out = [keyframes[0]]
    for kf in keyframes[1:-1]:
        prev = out[-1]
        dt = float(kf["frame"]) - float(prev["frame"])
        dv = abs(float(kf["value"]) - float(prev["value"]))
        if dt < min_frame_interval and dv <= value_threshold:
            continue
        out.append(kf)
    out.append(keyframes[-1])
    return out


def _remove_duplicate_sequences(
    keyframes: list[dict[str, Any]],
    eps_frame: float = 1e-6,
    eps_value: float | None = None,
) -> list[dict[str, Any]]:
    if not keyframes:
        return keyframes
    ev = eps_frame if eps_value is None else eps_value
    filtered = [keyframes[0]]
    for kf in keyframes[1:]:
        prev = filtered[-1]
        if close_enough(prev["frame"], kf["frame"], eps_frame) and close_enough(
            prev["value"], kf["value"], ev
        ):
            continue
        filtered.append(kf)
    return filtered


def _detect_linear_ramp(
    keyframes: list[dict[str, Any]],
    frame_eps: float = 1e-5,
    value_eps: float = 1e-5,
    min_points: int = 3,
) -> dict[str, Any] | None:
    if len(keyframes) < max(2, min_points):
        return None

    if any(k.get("interpolation") != "LINEAR" for k in keyframes):
        return None

    frames = [float(k["frame"]) for k in keyframes]
    values = [float(k["value"]) for k in keyframes]

    frame_steps = [frames[i + 1] - frames[i] for i in range(len(frames) - 1)]
    value_steps = [values[i + 1] - values[i] for i in range(len(values) - 1)]

    if any(abs(step) <= frame_eps for step in frame_steps):
        return None

    frame_step = frame_steps[0]
    value_step = value_steps[0]

    if any(abs(step - frame_step) > frame_eps for step in frame_steps):
        return None
    if any(abs(step - value_step) > value_eps for step in value_steps):
        return None

    return {
        "algorithm": "linear_ramp",
        "frame_start": frames[0],
        "frame_step": frame_step,
        "count": len(frames),
        "value_start": values[0],
        "value_step": value_step,
    }


def _contains_any_keyword(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in keywords if keyword)


def _matches_any_pattern(text: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(text, pattern) for pattern in patterns if pattern)


def _extract_animated_bones(action: dict[str, Any]) -> list[str]:
    bones: set[str] = set()
    for fc in action.get("fcurves", []):
        name = parse_bone_name(fc.get("data_path", ""))
        if name:
            bones.add(name)
    return sorted(bones)


def _strip_keyframe_for_output(
    kf: dict[str, Any], opt: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {"frame": kf["frame"], "value": kf["value"]}

    interp = kf.get("interpolation", "BEZIER")
    omit_default = bool(opt.get("omit_default_interpolation", True))
    if not omit_default or interp != "BEZIER":
        out["interpolation"] = interp

    omit_handles = bool(opt.get("omit_handles_for_linear_constant", True))
    if interp == "BEZIER":
        if "handle_left" in kf:
            out["handle_left"] = kf["handle_left"]
        if "handle_right" in kf:
            out["handle_right"] = kf["handle_right"]
    elif not omit_handles:
        if "handle_left" in kf:
            out["handle_left"] = kf["handle_left"]
        if "handle_right" in kf:
            out["handle_right"] = kf["handle_right"]

    return out


def optimize_payload(
    raw: dict[str, Any], opt_cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    opt = {**DEFAULT_OPT, **(opt_cfg or {})}
    scene_fps = float(raw.get("scene", {}).get("fps", 24.0) or 24.0)
    min_frame_interval = float(opt.get("dedupe_interval_sec", 0.0)) * scene_fps
    out: dict[str, Any] = {
        "meta": {
            "format": "optimized_animation_toml",
            "version": "1.0",
            "source": {
                "format": raw.get("meta", {}).get("format", "raw_animation_toml"),
                "version": raw.get("meta", {}).get("version", "1.0"),
            },
            "optimization": copy.deepcopy(opt),
        },
        "scene": copy.deepcopy(raw.get("scene", {})),
        "armature_bindings": [],
        "object_bindings": [],
        "shape_key_bindings": [],
        "optimized_actions": [],
    }

    action_by_name = {a.get("name"): a for a in raw.get("actions", [])}

    for action in raw.get("actions", []):
        action_name = str(action.get("name", ""))
        include_patterns = [str(x) for x in opt.get("action_include_patterns", [])]
        exclude_patterns = [str(x) for x in opt.get("action_exclude_patterns", [])]
        if include_patterns and not _matches_any_pattern(action_name, include_patterns):
            continue
        if exclude_patterns and _matches_any_pattern(action_name, exclude_patterns):
            continue

        optimized_action = {
            "name": action_name,
            "frame_range": copy.deepcopy(action.get("frame_range", [1.0, 1.0])),
            "procedural_patterns": [],
            "fcurves": [],
        }

        for fc in action.get("fcurves", []):
            data_path = str(fc.get("data_path", ""))
            kind = fcurve_kind(data_path)
            threshold = _threshold_for_kind(kind, opt)

            keys = copy.deepcopy(fc.get("keyframes", []))
            prune_excluded = _contains_any_keyword(
                data_path,
                [str(x) for x in opt.get("prune_exclude_data_path_keywords", [])],
            )

            if (
                bool(opt.get("enable_duplicate_keyframe_prune", True))
                and not prune_excluded
            ):
                keys = _remove_duplicate_sequences(
                    keys,
                    float(opt.get("duplicate_epsilon_frame", 1e-6)),
                    float(opt.get("duplicate_epsilon_value", 1e-6)),
                )
            else:
                keys = _remove_duplicate_sequences(keys)

            if bool(opt.get("enable_bezier_to_linear", True)) and not prune_excluded:
                b2l_threshold = float(opt.get("bezier_to_linear_threshold", 0.01))
                keys = _simplify_bezier_to_linear(keys, b2l_threshold)

            if bool(opt.get("enable_interval_dedupe", True)) and not prune_excluded:
                keys = _remove_dense_redundant_keyframes(
                    keys,
                    min_frame_interval=min_frame_interval,
                    value_threshold=float(
                        opt.get("interval_dedupe_value_threshold", threshold)
                    ),
                )

            if (
                bool(opt.get("enable_minor_keyframe_prune", True))
                and not prune_excluded
            ):
                keys = _remove_minor_keyframes(keys, threshold)

            if bool(opt.get("enable_rdp_simplify", True)) and not prune_excluded:
                rdp_eps = float(opt.get("rdp_epsilon", 0.001))
                keys = _rdp_simplify(keys, rdp_eps)

            if not keys:
                continue

            if len(keys) < int(opt.get("min_keyframes_per_fcurve", 1)):
                continue

            fc_out: dict[str, Any] = {
                "data_path": data_path,
                "array_index": int(fc.get("array_index", 0)),
            }

            if not bool(opt.get("omit_kind_field", True)):
                fc_out["kind"] = kind

            extrapolation = fc.get("extrapolation", "CONSTANT")
            if (
                not bool(opt.get("omit_default_extrapolation", True))
                or extrapolation != "CONSTANT"
            ):
                fc_out["extrapolation"] = extrapolation

            fc_out["keyframes"] = [_strip_keyframe_for_output(kf, opt) for kf in keys]

            pattern_excluded = _contains_any_keyword(
                data_path,
                [str(x) for x in opt.get("pattern_exclude_data_path_keywords", [])],
            )
            ramp = None
            if (
                bool(opt.get("enable_linear_pattern_detection", True))
                and not pattern_excluded
            ):
                ramp = _detect_linear_ramp(
                    keys,
                    frame_eps=float(opt.get("linear_pattern_frame_epsilon", 1e-5)),
                    value_eps=float(opt.get("linear_pattern_value_epsilon", 1e-5)),
                    min_points=int(opt.get("linear_pattern_min_points", 3)),
                )
            if ramp:
                pattern = {
                    "target_data_path": fc_out["data_path"],
                    "target_array_index": fc_out["array_index"],
                    **ramp,
                }
                optimized_action["procedural_patterns"].append(pattern)
                if not bool(opt.get("keep_procedural_source_keyframes", False)):
                    fc_out["keyframes"] = []

            optimized_action["fcurves"].append(fc_out)

        if optimized_action["fcurves"] or bool(opt.get("keep_empty_actions", False)):
            out["optimized_actions"].append(optimized_action)

    kept_action_names = {a["name"] for a in out["optimized_actions"]}

    for binding in raw.get("armature_bindings", []):
        if binding.get("action") not in kept_action_names:
            continue
        action = action_by_name.get(binding.get("action"))
        animated_bones = _extract_animated_bones(action) if action else []
        new_binding = copy.deepcopy(binding)
        if animated_bones:
            new_binding["animated_bones"] = animated_bones
        out["armature_bindings"].append(new_binding)

    for binding in raw.get("object_bindings", []):
        if binding.get("action") not in kept_action_names:
            continue
        out["object_bindings"].append(copy.deepcopy(binding))

    for binding in raw.get("shape_key_bindings", []):
        if binding.get("action") not in kept_action_names:
            continue
        out["shape_key_bindings"].append(copy.deepcopy(binding))

    return out


def _count_total_keyframes_from_actions(
    actions: list[dict[str, Any]], key_name: str
) -> int:
    total = 0
    for action in actions:
        for fc in action.get("fcurves", []):
            total += len(fc.get(key_name, []))
    return total


def _count_procedural_pattern_keyframes(actions: list[dict[str, Any]]) -> int:
    total = 0
    for action in actions:
        for pattern in action.get("procedural_patterns", []):
            total += int(pattern.get("count", 0))
    return total


def _build_report(raw: dict[str, Any], optimized: dict[str, Any]) -> dict[str, Any]:
    raw_actions = raw.get("actions", [])
    optimized_actions = optimized.get("optimized_actions", [])

    input_keyframes = _count_total_keyframes_from_actions(raw_actions, "keyframes")
    output_keyframes = _count_total_keyframes_from_actions(
        optimized_actions, "keyframes"
    )

    procedural_pattern_count = 0
    for action in optimized_actions:
        procedural_pattern_count += len(action.get("procedural_patterns", []))

    procedural_keyframes = _count_procedural_pattern_keyframes(optimized_actions)
    deleted_keyframes = max(
        0, input_keyframes - output_keyframes - procedural_keyframes
    )
    compression_ratio = (
        float(deleted_keyframes / input_keyframes) if input_keyframes else 0.0
    )
    keep_ratio = (
        float((output_keyframes + procedural_keyframes) / input_keyframes)
        if input_keyframes
        else 1.0
    )

    return {
        "meta": {
            "format": "optimization_report_toml",
            "version": "1.0",
        },
        "summary": {
            "input_actions": len(raw_actions),
            "output_actions": len(optimized_actions),
            "input_keyframes": input_keyframes,
            "output_keyframes": output_keyframes,
            "procedural_keyframes": procedural_keyframes,
            "deleted_keyframes": deleted_keyframes,
            "procedural_patterns": procedural_pattern_count,
            "compression_ratio": compression_ratio,
            "keep_ratio": keep_ratio,
            "compression_percent": compression_ratio * 100.0,
        },
    }


def convert_file(
    old_toml_path: str,
    new_toml_path: str,
    optimize_cfg: dict[str, Any] | None = None,
    report_toml_path: str | None = None,
) -> dict[str, Any]:
    raw = load_toml(old_toml_path)
    optimized = optimize_payload(raw, optimize_cfg)
    Path(new_toml_path).parent.mkdir(parents=True, exist_ok=True)
    dump_toml(optimized, new_toml_path)

    report = _build_report(raw, optimized)
    if report_toml_path:
        Path(report_toml_path).parent.mkdir(parents=True, exist_ok=True)
        dump_toml(report, report_toml_path)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optimize raw animation TOML to compact TOML"
    )
    parser.add_argument("--input", required=True, help="old.toml path")
    parser.add_argument("--output", required=True, help="new.toml path")
    parser.add_argument("--report", help="optional report.toml path")
    parser.add_argument("--opt-config", help="optional optimize config TOML path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    optimize_cfg = None
    if args.opt_config:
        optimize_cfg = load_toml(args.opt_config).get("optimize", {})
    report = convert_file(args.input, args.output, optimize_cfg, args.report)
    print(f"[old_to_new.py] optimized: {args.output}")
    if args.report:
        print(f"[old_to_new.py] report: {args.report}")
    print(
        "[old_to_new.py] summary: "
        f"deleted_keyframes={report['summary']['deleted_keyframes']}, "
        f"procedural_patterns={report['summary']['procedural_patterns']}, "
        f"compression={report['summary']['compression_percent']:.2f}%"
    )


if __name__ == "__main__":
    main()
