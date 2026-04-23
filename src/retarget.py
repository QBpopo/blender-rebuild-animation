from __future__ import annotations

import argparse
import copy
import re
from pathlib import Path
from typing import Any

from toml_io import dump_toml, load_toml


def remap_bone_in_data_path(data_path: str, old_bone: str, new_bone: str) -> str:
    pattern = re.compile(r'pose\.bones\["' + re.escape(old_bone) + r'"\]')
    return pattern.sub(f'pose.bones["{new_bone}"]', data_path)


def retarget_payload(
    payload: dict[str, Any], bone_map: dict[str, str]
) -> dict[str, Any]:
    result = copy.deepcopy(payload)

    for action in result.get("actions", []):
        for fc in action.get("fcurves", []):
            dp = fc.get("data_path", "")
            for old_bone, new_bone in bone_map.items():
                dp = remap_bone_in_data_path(dp, old_bone, new_bone)
            fc["data_path"] = dp

    for binding in result.get("armature_bindings", []):
        action_name = binding.get("action")
        matching_actions = [
            a for a in result.get("actions", []) if a.get("name") == action_name
        ]
        if matching_actions:
            action = matching_actions[0]
            animated_bones = set()
            for fc in action.get("fcurves", []):
                m = re.search(r'pose\.bones\["([^"]+)"\]', fc.get("data_path", ""))
                if m:
                    animated_bones.add(m.group(1))
            if animated_bones:
                binding["animated_bones"] = sorted(animated_bones)

    return result


def retarget_file(
    input_toml: str, output_toml: str, bone_map: dict[str, str]
) -> None:
    payload = load_toml(input_toml)
    result = retarget_payload(payload, bone_map)
    Path(output_toml).parent.mkdir(parents=True, exist_ok=True)
    dump_toml(result, output_toml)
    print(f"[retarget.py] retargeted: {output_toml}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retarget animation data by remapping bone names"
    )
    parser.add_argument("--input", required=True, help="input TOML path")
    parser.add_argument("--output", required=True, help="output TOML path")
    parser.add_argument(
        "--bone-map",
        nargs="+",
        required=True,
        help="bone mappings in format OLD_BONE:NEW_BONE",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    bone_map = {}
    for mapping in args.bone_map:
        if ":" not in mapping:
            raise ValueError(f"Invalid bone map format: {mapping}")
        old_bone, new_bone = mapping.split(":", 1)
        bone_map[old_bone] = new_bone
    retarget_file(args.input, args.output, bone_map)


if __name__ == "__main__":
    main()
