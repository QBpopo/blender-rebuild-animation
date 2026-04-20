from __future__ import annotations

import shutil
from pathlib import Path


def resolve_path(base: str | Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (Path(base) / path).resolve()


def ensure_parent(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def stem_with_suffix(path: str | Path, suffix: str, ext: str) -> Path:
    p = Path(path)
    return p.with_name(f"{p.stem}{suffix}{ext}")


def copy_blend_file(src: str | Path, dst: str | Path) -> None:
    ensure_parent(dst)
    shutil.copy2(src, dst)
