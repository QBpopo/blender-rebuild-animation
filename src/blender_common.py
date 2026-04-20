from __future__ import annotations

import sys
from pathlib import Path


def import_utils() -> None:
    current = Path(__file__).resolve().parent
    if str(current) not in sys.path:
        sys.path.insert(0, str(current))


def blender_argv() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]
