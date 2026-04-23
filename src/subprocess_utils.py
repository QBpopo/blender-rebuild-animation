from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_subprocess(args: list[str], cwd: str | Path | None = None) -> None:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed ({result.returncode}): {' '.join(args)}"
            )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Executable not found: {args[0]}") from exc
