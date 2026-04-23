from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from merge import merge_toml_files
from old_to_new import convert_file
from path_utils import copy_blend_file, ensure_parent, resolve_path
from retarget import retarget_file
from subprocess_utils import run_subprocess
from toml_io import load_toml


def _normalize_task_sources(
    task: dict[str, Any], config_path: Path
) -> list[dict[str, Any]]:
    if "sources" in task:
        sources = []
        for src in task["sources"]:
            resolved = resolve_path(config_path.parent, src["path"])
            label = src.get("label") or resolved.stem
            weight_ranges = src.get("weight_ranges", [])
            sources.append(
                {
                    "path": resolved,
                    "label": label,
                    "weight_ranges": weight_ranges,
                }
            )
        return sources

    sources = []
    if "old_blend" in task:
        main_path = resolve_path(config_path.parent, task["old_blend"])
        sources.append(
            {
                "path": main_path,
                "label": main_path.stem,
                "weight_ranges": [],
            }
        )

    extra_blends = task.get("extra_old_blends", [])
    extra_labels = task.get("extra_source_labels", [])
    for i, blend_path in enumerate(extra_blends):
        resolved = resolve_path(config_path.parent, blend_path)
        label = extra_labels[i] if i < len(extra_labels) else resolved.stem
        sources.append(
            {
                "path": resolved,
                "label": label,
                "weight_ranges": [],
            }
        )

    return sources


def _build_task_paths(
    config_path: Path, task: dict[str, Any], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    if not sources:
        raise ValueError("构建任务路径失败：sources 列表为空")

    task_label = task.get("label", "task")
    use_cache = bool(task.get("cache", False))
    dt = datetime.now().strftime("%Y%m%d_%H%M%S")

    if "new_blend" in task and task["new_blend"]:
        template_blend = resolve_path(config_path.parent, task["new_blend"])
        output_base = template_blend.parent / f"{task_label}.tmp"
    else:
        first_source_path = sources[0]["path"]
        template_blend = first_source_path
        output_base = first_source_path.parent / f"{task_label}.tmp"

    first_source_label = sources[0]["label"]

    old_toml = output_base / f"{first_source_label}.toml"

    if use_cache:
        new_toml = output_base / f"{task_label}.{dt}.toml"
        report_toml = output_base / f"{task_label}.{dt}.report.toml"
        new_blend = output_base / f"{task_label}.{dt}.blend"
    else:
        new_toml = output_base / f"{task_label}.toml"
        report_toml = output_base / f"{task_label}.report.toml"
        new_blend = output_base / f"{task_label}.blend"

    return {
        "sources": sources,
        "template_blend": template_blend,
        "new_blend": new_blend,
        "old_toml": old_toml,
        "new_toml": new_toml,
        "report_toml": report_toml,
    }


def _run_old_export(
    blender_exe: Path, src_dir: Path, old_blend: Path, old_toml: Path
) -> None:
    ensure_parent(old_toml)
    run_subprocess(
        [
            str(blender_exe),
            "--background",
            str(old_blend),
            "--python",
            str(src_dir / "old.py"),
            "--",
            "--output",
            str(old_toml),
        ]
    )


def _run_engine_rebuild(
    blender_exe: Path, src_dir: Path, new_blend: Path, new_toml: Path
) -> None:
    ensure_parent(new_blend)
    run_subprocess(
        [
            str(blender_exe),
            "--background",
            str(new_blend),
            "--python",
            str(src_dir / "engine.py"),
            "--",
            "--input",
            str(new_toml),
            "--output",
            str(new_blend),
        ]
    )


def _run_merge_step(
    source_tomls: list[Path],
    output_toml: Path,
    source_labels: list[str],
    merge_cfg: dict[str, Any],
    source_weights: list[list[dict[str, Any]]],
) -> None:
    all_inputs = [str(p) for p in source_tomls]
    merge_toml_files(
        all_inputs, str(output_toml), source_labels, merge_cfg, source_weights
    )


def run_pipeline(config_path: Path) -> None:
    config = load_toml(config_path)
    blender_exe = resolve_path(config_path.parent, config["blender"]["exe"])
    if not blender_exe.exists():
        raise FileNotFoundError(f"Blender 可执行文件不存在: {blender_exe}")
    src_dir = Path(__file__).resolve().parent

    optimize_cfg = config.get("optimize", {})
    merge_cfg = config.get("merge", {})
    tasks = config.get("tasks", [])
    if not tasks:
        raise ValueError("config.toml 缺少 [[tasks]]")

    for idx, task in enumerate(tasks, start=1):
        sources = _normalize_task_sources(task, config_path)
        if not sources:
            raise ValueError(f"Task {idx} 缺少源文件")

        task_info = _build_task_paths(config_path, task, sources)
        resolved_sources = task_info["sources"]
        template_blend = task_info["template_blend"]
        new_blend = task_info["new_blend"]
        old_toml = task_info["old_toml"]
        new_toml = task_info["new_toml"]
        report_toml = task_info["report_toml"]

        for src in resolved_sources:
            if not src["path"].exists():
                raise FileNotFoundError(f"源文件不存在: {src['path']}")

        if not template_blend.exists():
            raise FileNotFoundError(f"模板文件不存在: {template_blend}")

        if not task.get("cache", False):
            if new_blend.exists():
                new_blend.unlink()
            backup_blend = new_blend.parent / f"{new_blend.name}1"
            if backup_blend.exists():
                backup_blend.unlink()
        copy_blend_file(template_blend, new_blend)

        has_merge = len(resolved_sources) > 1
        bone_map = task.get("bone_map", {})

        all_labels = [src["label"] for src in resolved_sources]
        all_weight_ranges = [src["weight_ranges"] for src in resolved_sources]

        if has_merge:
            print(f"\n=== Task {idx} (合并模式: {len(resolved_sources)} 个源文件) ===")
        else:
            print(f"\n=== Task {idx} ===")

        for src in resolved_sources:
            print(
                f"  source: {src['path']} (label={src['label']}, weights={src['weight_ranges'] or 'default'})"
            )
        print(f"new_blend : {new_blend}")
        print(f"toml_dir  : {old_toml.parent}")

        step_count = 5 if (has_merge or bone_map) else 3
        step_num = 1

        source_tomls: list[Path] = []
        for src_idx, src in enumerate(resolved_sources):
            if has_merge:
                toml_path = old_toml.parent / f"{src['label']}_old.toml"
            else:
                toml_path = old_toml

            if toml_path.exists():
                print(
                    f"[{step_num}/{step_count}] 跳过导出（已存在 {toml_path.name}）"
                )
            else:
                if has_merge:
                    print(
                        f"[{step_num}/{step_count}] 导出源 {src_idx + 1}/{len(resolved_sources)}: {src['path'].name} ..."
                    )
                else:
                    print(f"[{step_num}/{step_count}] 导出 old.toml ...")
                _run_old_export(blender_exe, src_dir, src["path"], toml_path)
            source_tomls.append(toml_path)

        if has_merge:
            step_num += 1
            print(f"[{step_num}/{step_count}] 合并 {len(source_tomls)} 个 old.toml ...")
            _run_merge_step(
                source_tomls, old_toml, all_labels, merge_cfg, all_weight_ranges
            )

        if bone_map:
            step_num += 1
            retargeted_toml = old_toml.parent / f"{old_toml.stem}_retargeted{old_toml.suffix}"
            print(f"[{step_num}/{step_count}] 骨骼重映射 {bone_map} ...")
            retarget_file(str(old_toml), str(retargeted_toml), bone_map)
            old_toml = retargeted_toml

        step_num += 1
        print(f"[{step_num}/{step_count}] 优化到 new.toml ...")
        report = convert_file(
            str(old_toml), str(new_toml), optimize_cfg, str(report_toml)
        )
        print(
            "优化报告: "
            f"deleted_keyframes={report['summary']['deleted_keyframes']}, "
            f"procedural_patterns={report['summary']['procedural_patterns']}, "
            f"compression={report['summary']['compression_percent']:.2f}%"
        )
        step_num += 1

        print(f"[{step_num}/{step_count}] 重建动画到 new_blend ...")
        _run_engine_rebuild(blender_exe, src_dir, new_blend, new_toml)

        print(f"完成: {new_blend}")
        print(f"报告: {report_toml}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot blender animation reverse pipeline"
    )
    parser.add_argument(
        "--config", default=str(Path(__file__).resolve().parent / "config.toml")
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_pipeline(Path(args.config).resolve())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[main.py] ERROR: {exc}", file=sys.stderr)
        raise
