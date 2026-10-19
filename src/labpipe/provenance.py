"""Provenance records: enough to regenerate every output from provenance.json alone."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from labpipe.safety import PROJECT_ROOT

TRACKED_LIBRARIES = ["labpipe", "numpy", "scipy", "matplotlib", "PyYAML"]


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:  # read-only
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(["git", "-C", str(PROJECT_ROOT), *args],
                                   capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def git_info() -> dict[str, Any]:
    """Code identity. config/ is excluded from the dirty check: settings are
    recorded in full (with file hashes) in every provenance.json instead.
    Untracked files count only under src/ and references/, the only places they
    could change results (the Claude Code sandbox creates empty placeholder
    files such as .bashrc in the repository while a command runs)."""
    commit = _git("rev-parse", "HEAD")
    tracked = _git("status", "--porcelain", "--untracked-files=no", "--", ".",
                   ":(exclude)config")
    untracked = _git("status", "--porcelain", "--untracked-files=all", "--", "src",
                     "references")
    status = None if tracked is None or untracked is None else "\n".join(
        line for line in [tracked, *[row for row in untracked.splitlines()
                                     if row.startswith("??")]] if line)
    return {
        "commit": commit,
        "dirty": bool(status) if status is not None else None,
        "dirty_files": status.splitlines()[:50] if status else [],
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "remote": _git("config", "--get", "remote.origin.url"),
    }


def code_version() -> str:
    """Short identifier used in the manifest: commit, plus '+dirty' if modified."""
    info = git_info()
    commit = (info["commit"] or "uncommitted")[:12]
    return f"{commit}+dirty" if info["dirty"] else commit


def environment() -> dict[str, Any]:
    libraries = {}
    for name in TRACKED_LIBRARIES:
        try:
            libraries[name] = version(name)
        except PackageNotFoundError:
            libraries[name] = None
    lock = PROJECT_ROOT / "requirements.lock"
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "libraries": libraries,
        "requirements_lock_sha256": sha256_file(lock) if lock.exists() else None,
    }


def runtime() -> dict[str, Any]:
    """Where the analysis ran: inside the labpipe container or natively."""
    if os.environ.get("LABPIPE_IN_CONTAINER"):
        return {"mode": "container", "engine": os.environ.get("LABPIPE_ENGINE") or None,
                "image_id": os.environ.get("LABPIPE_IMAGE_ID") or None}
    return {"mode": "native", "engine": None, "image_id": None}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_record(*, method: str, entry_point: str, input_path: Path, input_sha256: str,
                 context: dict[str, Any], settings: dict[str, Any],
                 settings_sources: list[dict[str, Any]] | None, outputs: dict[str, str],
                 out_dir: Path, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The provenance.json every method writes (method-specific details go in ``extra``).

    Enough to regenerate the outputs: input path + sha256, code commit, the fully
    resolved settings (plus the files they came from), environment, output hashes.
    ``entry_point`` is "package.module.run"; `labpipe replay` imports it.
    """
    from labpipe.settings import fingerprint  # local import: settings imports safety only

    final_dir = context.get("output_dir") or str(out_dir)
    record = {
        "schema": "labpipe.provenance/1",
        "method": method,
        "created_utc": utc_now(),
        "input": {"path": str(input_path), "relative_path": context.get("relative_path"),
                  "raw_root": context.get("raw_root"), "sha256": input_sha256,
                  "size_bytes": Path(input_path).stat().st_size},
        "sample_id": context.get("sample_id"),
        "measurement_id": context.get("measurement_id"),
        "code": git_info() | {"labpipe_entry": entry_point},
        "parameters": {"sources": settings_sources or [], "resolved": settings,
                       "resolved_sha256": fingerprint(settings)},
        "environment": environment(),
        "runtime": runtime(),
        "outputs": outputs,
        "command": " ".join(shlex.quote(arg) for arg in sys.argv),
        "output_dir": final_dir,
        "replay": "labpipe replay " + shlex.quote(str(Path(final_dir) / "provenance.json")),
    }
    record.update(extra or {})
    return record
