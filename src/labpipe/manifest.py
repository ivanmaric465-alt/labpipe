"""manifest.json: one entry per raw file the pipeline has seen.

Entry status values:
  pending       new or changed file, waiting for analysis
  done          analysed; outputs in output_dir (review flag says if a human should look)
  failed        analysis failed; reason recorded; retried only when the file hash
                or the code version changes, at most max_attempts_per_hash times
  needs_review  cannot proceed without a human (e.g. unclear sample ID)
  duplicate     same measurement as another entry (e.g. .xy next to .xrdml)
  ignored       matched an ignore pattern (e.g. .rd)
  stale         analysed with an older code version or different settings;
                reprocessed only on request
  missing       file no longer present under the raw root (outputs kept)
  baseline      already there when labpipe was set up and the user chose "only new
                files"; not analysed until its content changes or it is requeued

Writes are atomic and serialised with an exclusive lock on logs/.manifest.mutex
(labpipe.locking: fcntl on Linux/macOS, msvcrt on Windows).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Iterator

from labpipe.locking import exclusive
from labpipe.provenance import utc_now
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, atomic_write_text

SCHEMA = "labpipe.manifest/1"


def manifest_path(config: dict) -> Path:
    return PROJECT_ROOT / config["manifest"]


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "updated_utc": None, "entries": {}}


def load(config: dict) -> dict[str, Any]:
    path = manifest_path(config)
    if not path.exists():
        return _empty()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise RuntimeError(f"unexpected manifest schema {data.get('schema')!r}")
    return data


def save(config: dict, data: dict[str, Any]) -> None:
    data["updated_utc"] = utc_now()
    text = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n"
    atomic_write_text(manifest_path(config), text, config)


@contextlib.contextmanager
def locked(config: dict) -> Iterator[dict[str, Any]]:
    """Exclusive read-modify-write of the manifest."""
    lock_path = PROJECT_ROOT / config["logs_dir"] / ".manifest.mutex"
    assert_pipeline_writable(lock_path, config)
    with exclusive(lock_path):
        data = load(config)
        yield data
        save(config, data)


def set_status(entry: dict[str, Any], status: str, reason: str | None = None) -> None:
    if entry.get("status") != status or entry.get("status_reason") != reason:
        entry.setdefault("events", []).append(
            {"utc": utc_now(), "from": entry.get("status"), "to": status, "reason": reason})
        entry["events"] = entry["events"][-30:]
    entry["status"] = status
    entry["status_reason"] = reason
