from __future__ import annotations

import subprocess
from pathlib import Path


def run_subprocess(args: list[str], cwd: str | Path | None = None) -> None:
    completed = subprocess.run(args, cwd=str(cwd) if cwd else None, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(args)}")
