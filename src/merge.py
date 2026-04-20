from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

from toml_io import dump_toml, load_toml


DEFAULT_MERGE_CFG = {
    "action_conflict_strategy": "rename",
    "action_rename_template": "{source}.{name}",
    "binding_conflict_strategy": "keep_last",
    "scene_conflict_strategy": "union",
}


def _effective_weight_in_range(
    weight_ranges: list[dict[str, Any]] | None,
    frame_start: float,
    frame_end: float,
) -> float:
    if not weight_ranges:
        return max(0.0, frame_end - frame_start) * 1.0
    total = 0.0
    for wr in weight_ranges:
        wr_start = float(wr.get("frame_start", 0))
        wr_end = float(wr.get("frame_end", 1e9))
        wr_weight = float(wr.get("weight", 1.0))
        overlap_start = max(wr_start, frame_start)
        overlap_end = min(wr_end, frame_end)
        if overlap_start < overlap_end:
            total += wr_weight * (overlap_end - overlap_start)
    return total


def _total_source_weight(
    weight_ranges: list[dict[str, Any]] | None,
) -> float:
    if not weight_ranges:
        return 1.0
    total = 0.0
    for wr in weight_ranges:
        wr_start = float(wr.get("frame_start", 0))
        wr_end = float(wr.get("frame_end", 1e9))
        wr_weight = float(wr.get("weight", 1.0))
        span = wr_end - wr_start
        if span > 0:
            total += wr_weight * span
        else:
            total += wr_weight
    return total


def _compute_dominant_sources(
    all_actions: list[list[dict[str, Any]]],
    source_weights: list[list[dict[str, Any]]],
) -> dict[str, int]:
    action_sources: dict[str, list[int]] = {}
    for source_idx, actions in enumerate(all_actions):
        for action in actions:
            name = str(action.get("name", ""))
            if name not in action_sources:
                action_sources[name] = []
            action_sources[name].append(source_idx)

    dominant: dict[str, int] = {}
    for name, source_indices in action_sources.items():
        if len(source_indices) <= 1:
            dominant[name] = source_indices[0]
            continue

        best_idx = source_indices[0]
        best_weight = -1.0
        for idx in source_indices:
            for action in all_actions[idx]:
                if str(action.get("name", "")) == name:
                    fr = action.get("frame_range", [1.0, 1.0])
                    w = source_weights[idx] if idx < len(source_weights) else None
                    weight = _effective_weight_in_range(w, float(fr[0]), float(fr[1]))
                    if weight > best_weight:
                        best_weight = weight
                        best_idx = idx
                    break
        dominant[name] = best_idx

    return dominant


def _generate_renamed_name(
    name: str,
    source_label: str,
    existing_names: set[str],
    rename_template: str,
) -> str:
    new_name = rename_template.format(source=source_label, name=name)
    if new_name not in existing_names:
        return new_name

    counter = 2
    while True:
        candidate = f"{new_name}.{counter}"
        if candidate not in existing_names:
            return candidate
        counter += 1


def _resolve_action_name(
    name: str,
    source_label: str,
    existing_names: set[str],
    strategy: str,
    rename_template: str,
    is_dominant: bool = False,
) -> str:
    if name not in existing_names:
        return name

    if strategy == "keep_first":
        return name
    if strategy == "keep_last":
        return name
    if strategy == "merge_fcurves":
        return name
    if strategy == "weighted":
        if is_dominant:
            return name
        return _generate_renamed_name(
            name, source_label, existing_names, rename_template
        )

    return _generate_renamed_name(name, source_label, existing_names, rename_template)


def _merge_scene(scenes: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    if not scenes:
        return {"name": "Scene", "frame_start": 1, "frame_end": 250, "fps": 24.0}

    if len(scenes) == 1:
        return copy.deepcopy(scenes[0])

    if strategy == "keep_first":
        return copy.deepcopy(scenes[0])

    if strategy == "keep_last":
        return copy.deepcopy(scenes[-1])

    if strategy in ("union", "weighted"):
        result = copy.deepcopy(scenes[0])
        min_start = min(int(s.get("frame_start", 1)) for s in scenes)
        max_end = max(int(s.get("frame_end", 250)) for s in scenes)
        max_fps = max(float(s.get("fps", 24.0)) for s in scenes)
        result["frame_start"] = min_start
        result["frame_end"] = max_end
        result["fps"] = max_fps
        return result

    return copy.deepcopy(scenes[0])


def _merge_bindings(
    all_bindings: list[list[dict[str, Any]]],
    strategy: str,
    action_name_map: dict[str, dict[str, str]],
    source_weights: list[list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    seen_source_idx: dict[str, int] = {}

    for source_idx, bindings in enumerate(all_bindings):
        for binding in bindings:
            obj_name = binding.get("object", "")
            key = f"{obj_name}"

            remapped = copy.deepcopy(binding)
            original_action = binding.get("action", "")
            source_map = action_name_map.get(str(source_idx), {})
            if original_action in source_map:
                remapped["action"] = source_map[original_action]

            if strategy == "keep_first":
                if key not in seen:
                    seen[key] = remapped
                    seen_source_idx[key] = source_idx
            elif strategy == "keep_last":
                seen[key] = remapped
                seen_source_idx[key] = source_idx
            elif strategy == "weighted" and source_weights is not None:
                cur_weight = _total_source_weight(
                    source_weights[source_idx]
                    if source_idx < len(source_weights)
                    else None
                )
                if key not in seen:
                    seen[key] = remapped
                    seen_source_idx[key] = source_idx
                else:
                    prev_weight = _total_source_weight(
                        source_weights[seen_source_idx[key]]
                        if seen_source_idx[key] < len(source_weights)
                        else None
                    )
                    if cur_weight > prev_weight:
                        seen[key] = remapped
                        seen_source_idx[key] = source_idx
            else:
                seen[key] = remapped
                seen_source_idx[key] = source_idx

    return list(seen.values())


def _merge_actions(
    all_actions: list[list[dict[str, Any]]],
    source_labels: list[str],
    strategy: str,
    rename_template: str,
    source_weights: list[list[dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    dominant_map: dict[str, int] = {}
    if strategy == "weighted" and source_weights is not None:
        dominant_map = _compute_dominant_sources(all_actions, source_weights)

    items: list[tuple[int, dict[str, Any]]] = []
    for source_idx, actions in enumerate(all_actions):
        for action in actions:
            items.append((source_idx, action))

    if strategy == "weighted" and dominant_map:

        def _sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
            si, act = item
            name = str(act.get("name", ""))
            is_dom = dominant_map.get(name) == si
            return (0 if is_dom else 1, si)

        items.sort(key=_sort_key)

    result: list[dict[str, Any]] = []
    action_name_map: dict[str, dict[str, str]] = {}
    existing_names: set[str] = set()
    existing_by_name: dict[str, dict[str, Any]] = {}

    for source_idx, action in items:
        source_label = (
            source_labels[source_idx]
            if source_idx < len(source_labels)
            else str(source_idx)
        )
        if str(source_idx) not in action_name_map:
            action_name_map[str(source_idx)] = {}

        original_name = str(action.get("name", ""))
        is_dominant = (
            dominant_map.get(original_name) == source_idx if dominant_map else False
        )

        resolved_name = _resolve_action_name(
            original_name,
            source_label,
            existing_names,
            strategy,
            rename_template,
            is_dominant,
        )
        action_name_map[str(source_idx)][original_name] = resolved_name

        if resolved_name in existing_names:
            if strategy == "merge_fcurves":
                existing = existing_by_name[resolved_name]
                existing_fcurves = existing.get("fcurves", [])
                new_fcurves = action.get("fcurves", [])
                existing_keys = {
                    (fc.get("data_path"), fc.get("array_index"))
                    for fc in existing_fcurves
                }
                for fc in new_fcurves:
                    fc_key = (fc.get("data_path"), fc.get("array_index"))
                    if fc_key not in existing_keys:
                        existing_fcurves.append(copy.deepcopy(fc))
                    else:
                        for idx, efc in enumerate(existing_fcurves):
                            if (
                                efc.get("data_path"),
                                efc.get("array_index"),
                            ) == fc_key:
                                existing_fcurves[idx] = copy.deepcopy(fc)
                                break

                old_range = existing.get("frame_range", [1.0, 1.0])
                new_range = action.get("frame_range", [1.0, 1.0])
                existing["frame_range"] = [
                    min(float(old_range[0]), float(new_range[0])),
                    max(float(old_range[1]), float(new_range[1])),
                ]
            elif strategy == "keep_last":
                for i, existing in enumerate(result):
                    if existing.get("name") == resolved_name:
                        new_action = copy.deepcopy(action)
                        new_action["name"] = resolved_name
                        result[i] = new_action
                        existing_by_name[resolved_name] = new_action
                        break
            elif strategy == "weighted" and is_dominant:
                for i, existing in enumerate(result):
                    if existing.get("name") == resolved_name:
                        new_action = copy.deepcopy(action)
                        new_action["name"] = resolved_name
                        result[i] = new_action
                        existing_by_name[resolved_name] = new_action
                        break
        else:
            new_action = copy.deepcopy(action)
            new_action["name"] = resolved_name
            result.append(new_action)
            existing_names.add(resolved_name)
            existing_by_name[resolved_name] = new_action

    return result, action_name_map


def merge_payloads(
    payloads: list[dict[str, Any]],
    source_labels: list[str] | None = None,
    merge_cfg: dict[str, Any] | None = None,
    source_weights: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if not payloads:
        raise ValueError("没有可合并的数据")

    cfg = {**DEFAULT_MERGE_CFG, **(merge_cfg or {})}
    labels = source_labels or [f"source_{i}" for i in range(len(payloads))]

    if len(payloads) == 1:
        return copy.deepcopy(payloads[0])

    action_strategy = str(cfg.get("action_conflict_strategy", "rename"))
    rename_template = str(cfg.get("action_rename_template", "{source}.{name}"))
    binding_strategy = str(cfg.get("binding_conflict_strategy", "keep_last"))
    scene_strategy = str(cfg.get("scene_conflict_strategy", "union"))

    merged_scene = _merge_scene([p.get("scene", {}) for p in payloads], scene_strategy)

    all_actions = [p.get("actions", []) for p in payloads]
    merged_actions, action_name_map = _merge_actions(
        all_actions, labels, action_strategy, rename_template, source_weights
    )

    all_armature = [p.get("armature_bindings", []) for p in payloads]
    all_object = [p.get("object_bindings", []) for p in payloads]
    all_shape = [p.get("shape_key_bindings", []) for p in payloads]

    merged_armature = _merge_bindings(
        all_armature, binding_strategy, action_name_map, source_weights
    )
    merged_object = _merge_bindings(
        all_object, binding_strategy, action_name_map, source_weights
    )
    merged_shape = _merge_bindings(
        all_shape, binding_strategy, action_name_map, source_weights
    )

    return {
        "meta": {
            "format": "raw_animation_toml",
            "version": "1.0",
            "merged_from": labels,
        },
        "scene": merged_scene,
        "armature_bindings": merged_armature,
        "object_bindings": merged_object,
        "shape_key_bindings": merged_shape,
        "actions": merged_actions,
    }


def merge_toml_files(
    input_paths: list[str],
    output_path: str,
    source_labels: list[str] | None = None,
    merge_cfg: dict[str, Any] | None = None,
    source_weights: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    for path in input_paths:
        payloads.append(load_toml(path))

    merged = merge_payloads(payloads, source_labels, merge_cfg, source_weights)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    dump_toml(merged, output_path)

    summary = {
        "sources": len(payloads),
        "total_actions": len(merged.get("actions", [])),
        "total_armature_bindings": len(merged.get("armature_bindings", [])),
        "total_object_bindings": len(merged.get("object_bindings", [])),
        "total_shape_key_bindings": len(merged.get("shape_key_bindings", [])),
        "total_keyframes": sum(
            len(fc.get("keyframes", []))
            for a in merged.get("actions", [])
            for fc in a.get("fcurves", [])
        ),
    }
    print(f"[merge.py] merged {summary['sources']} files → {output_path}")
    print(
        f"[merge.py] summary: "
        f"actions={summary['total_actions']}, "
        f"armature_bindings={summary['total_armature_bindings']}, "
        f"object_bindings={summary['total_object_bindings']}, "
        f"shape_key_bindings={summary['total_shape_key_bindings']}, "
        f"keyframes={summary['total_keyframes']}"
    )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge multiple raw animation TOML files into one"
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="input old.toml paths to merge",
    )
    parser.add_argument("--output", required=True, help="output merged TOML path")
    parser.add_argument(
        "--labels",
        nargs="*",
        default=[],
        help="source labels for rename strategy",
    )
    parser.add_argument("--merge-config", help="optional merge config TOML path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    merge_cfg = None
    if args.merge_config:
        merge_cfg = load_toml(args.merge_config).get("merge", {})
    labels = args.labels if args.labels else None
    merge_toml_files(args.inputs, args.output, labels, merge_cfg)


if __name__ == "__main__":
    main()
