#!/usr/bin/env python3
"""PreToolUse hook launcher for Claude Code and Codex: runs `labpipe guard` from labpipe's Python.

Standard library only, so it runs under any Python 3. Finds the Python of the installation
in this order: `.labpipe/python` of the project it serves (written by `labpipe project sync`),
LABPIPE_PYTHON, $LABPIPE_HOME/venv, the default locations (README). If none is found and the
project has config/local.yaml (labpipe is set up), or the guard cannot run, the call is
blocked (exit 2) with the fix. Without config/local.yaml (first-time setup) there is nothing
to protect yet, so the call is left to the normal permission rules.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

FIX = ("run `labpipe project sync` (writes .labpipe/python) or set LABPIPE_PYTHON to the "
       "Python of the labpipe installation")


def _venv_python(home: Path) -> Path:
    return home / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _recorded(folder: Path) -> list[Path]:
    try:
        lines = (folder / ".labpipe" / "python").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [Path(lines[0].strip())] if lines and lines[0].strip() else []


def _find_python(folder: Path) -> Path | None:
    candidates = _recorded(folder)
    if os.environ.get("LABPIPE_PYTHON"):
        candidates.append(Path(os.environ["LABPIPE_PYTHON"]))
    if os.environ.get("LABPIPE_HOME"):
        candidates.append(_venv_python(Path(os.environ["LABPIPE_HOME"])))
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        candidates.append(_venv_python(base / "labpipe"))
    else:
        candidates.append(_venv_python(Path.home() / ".local" / "share" / "labpipe"))
    return next((path for path in candidates if path.is_file()), None)


def main() -> int:
    event = sys.stdin.buffer.read()
    env = dict(os.environ)
    # The project is the folder this launcher lives in (hooks/guard.py in the repository,
    # .labpipe/hooks/guard.py in a project), whatever the harness's working directory.
    here = Path(__file__).resolve()
    folder = here.parents[2] if here.parents[1].name == ".labpipe" else here.parents[1]
    python = _find_python(folder)
    if python is None:
        if (folder / "config" / "local.yaml").is_file():
            print("labpipe guard: no labpipe Python found for this project; blocking to be "
                  f"safe. Fix: {FIX}", file=sys.stderr)
            return 2
        return 0  # labpipe not set up yet: no raw roots configured to protect
    if not (env.get("LABPIPE_PROJECT") or env.get("LABPIPE_ROOT")):
        env["LABPIPE_PROJECT"] = str(folder)
    try:
        completed = subprocess.run([str(python), "-m", "labpipe.cli", "guard"], input=event,
                                   capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"labpipe guard could not run ({exc}); blocking to be safe. Fix: {FIX}",
              file=sys.stderr)
        return 2
    sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    if completed.returncode not in (0, 2):
        print(f"labpipe guard failed; blocking to be safe. Fix: {FIX}", file=sys.stderr)
        return 2
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
