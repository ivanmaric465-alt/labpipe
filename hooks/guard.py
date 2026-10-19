#!/usr/bin/env python3
"""PreToolUse hook launcher for Claude Code and Codex: runs `labpipe guard` from the labpipe venv.

Standard library only, so it runs under any Python 3. Finds the venv via
LABPIPE_PYTHON, then the default locations (README). If labpipe is not
installed yet (first-time setup), nothing is configured to protect, so the
call is left to the normal permission rules. If labpipe is installed but the
guard cannot run, the call is blocked (exit 2).
"""

import os
import subprocess
import sys
from pathlib import Path


def _venv_python() -> Path | None:
    candidates = []
    if os.environ.get("LABPIPE_PYTHON"):
        candidates.append(Path(os.environ["LABPIPE_PYTHON"]))
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        candidates.append(base / "labpipe" / "venv" / "Scripts" / "python.exe")
    else:
        candidates.append(Path.home() / ".local" / "share" / "labpipe" / "venv" / "bin" / "python")
    return next((path for path in candidates if path.exists()), None)


def main() -> int:
    event = sys.stdin.buffer.read()
    python = _venv_python()
    if python is None:
        return 0  # labpipe not installed yet: no raw roots configured to protect
    env = dict(os.environ)
    # The project is the repository this launcher lives in (hooks/guard.py), whatever
    # the harness's working directory.
    env.setdefault("LABPIPE_ROOT", str(Path(__file__).resolve().parents[1]))
    try:
        completed = subprocess.run([str(python), "-m", "labpipe.cli", "guard"], input=event,
                                   capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"labpipe guard could not run ({exc}); blocking to be safe", file=sys.stderr)
        return 2
    sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
    if completed.returncode not in (0, 2):
        print("labpipe guard failed; blocking to be safe", file=sys.stderr)
        return 2
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
