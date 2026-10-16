"""Scan raw roots and bring manifest.json up to date. Idempotent.

* unchanged file (same sha256): left alone;
* changed file: previous state moved to the entry's history, status pending;
* done with an older code version or different settings: marked stale
  (not reprocessed automatically);
* failed: back to pending only if the code version changed and the attempt
  budget for this file hash is not used up;
* needs_review: back to pending once a human override resolves it;
* files modified within settle_minutes are skipped (a sync client or the instrument PC may still be writing);
* entries whose file disappeared: status missing (outputs are kept);
* baseline=True (setup, "only new files from now on"): files seen for the first
  time are recorded as baseline, not analysed; they become pending when their
  content changes or on `labpipe requeue --baseline | --entry ID`.

Raw files are only ever opened for reading (hashing).
"""

from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from labpipe import manifest as mf
from labpipe import settings as settings_mod
from labpipe.naming import derive_sample_id, entry_id, measurement_id
from labpipe.provenance import code_version, sha256_file, utc_now
from labpipe.registry import classify, load_methods
from labpipe.safety import PROJECT_ROOT


def _overrides(config: dict) -> dict[str, str]:
    path = PROJECT_ROOT / config["sample_id"]["overrides_file"]
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(data.get("overrides") or {})


def _walk(root: Path):
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        subdirectories.sort()
        for name in sorted(files):
            full = Path(directory) / name
            if full.is_symlink() or not full.is_file():
                continue
            yield full, full.relative_to(root).as_posix()


def scan(config: dict, *, baseline: bool = False) -> dict[str, Any]:
    methods = load_methods()
    overrides = _overrides(config)
    pattern = config["sample_id"]["pattern"]
    settle_seconds = 60 * config["settle_minutes"]
    max_attempts = config["max_attempts_per_hash"]
    current_code = code_version()
    now = time.time()
    counts: Counter[str] = Counter()
    changes: list[dict[str, Any]] = []

    with mf.locked(config) as data:
        entries = data["entries"]
        seen: set[str] = set()
        for root in config["raw_roots"]:
            root_path = Path(root["path"])
            if not root_path.is_dir():
                raise RuntimeError(f"raw root {root['id']} not available at {root_path} "
                                   "(is the read-only mount active?)")
            files = list(_walk(root_path))
            stems: dict[tuple[str, str], set[str]] = {}
            for _, relative in files:
                path = PurePosixPath(relative)
                stems.setdefault((str(path.parent), path.stem.lower()), set()).add(
                    path.suffix.lower())

            for full, relative in files:
                method, kind = classify(relative, methods)
                if method is None:
                    counts["unclaimed"] += 1
                    continue
                stat = full.stat()
                if now - stat.st_mtime < settle_seconds:
                    counts["settling"] += 1
                    continue
                spec = methods[method]
                eid = entry_id(method, root["id"], relative)
                seen.add(eid)
                digest = sha256_file(full)
                entry = entries.get(eid)
                is_new = entry is None
                if is_new:
                    entry = entries[eid] = {
                        "entry_id": eid, "method": method, "raw_root": root["id"],
                        "relative_path": relative, "first_seen_utc": utc_now(),
                        "attempts": 0, "history": [],
                    }
                previous_hash = entry.get("sha256")
                entry.update({"size_bytes": stat.st_size, "mtime_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
                    "subagent": spec.get("subagent")})

                path = PurePosixPath(relative)
                sample, source, problem = derive_sample_id(relative, pattern=pattern,
                                                           overrides=overrides)
                entry.update({"sample_id": sample, "sample_id_source": source,
                              "measurement_id": measurement_id(relative)})
                primary_sibling = (path.suffix.lower() in spec.get("duplicate_of_primary", [])
                                   and spec.get("primary_extension") in stems.get(
                                       (str(path.parent), path.stem.lower()), set()))

                if previous_hash is not None and previous_hash != digest:
                    entry["history"].append({
                        "sha256": previous_hash, "status": entry.get("status"),
                        "output_dir": entry.get("output_dir"),
                        "code_version": entry.get("code_version"), "replaced_utc": utc_now()})
                    entry["attempts"] = 0
                entry["sha256"] = digest

                old_status = entry.get("status")
                if kind == "ignore":
                    mf.set_status(entry, "ignored", "matches an ignore pattern in methods.yaml")
                elif primary_sibling:
                    primary = path.with_suffix(spec["primary_extension"]).as_posix()
                    entry["duplicate_of"] = entry_id(method, root["id"], primary)
                    mf.set_status(entry, "duplicate",
                                  f"same measurement as {primary} (primary format)")
                elif baseline and is_new:
                    mf.set_status(entry, "baseline", "already in the data folder when labpipe "
                                  "was set up; not analysed (labpipe requeue --baseline)")
                elif problem:
                    mf.set_status(entry, "needs_review", f"sample ID unclear: {problem}")
                elif is_new or previous_hash != digest or old_status in (None, "missing"):
                    mf.set_status(entry, "pending",
                                  "new file" if is_new else "file content changed"
                                  if previous_hash != digest else "file reappeared")
                elif old_status == "needs_review":
                    mf.set_status(entry, "pending", "review resolved")
                elif old_status == "done" and entry.get("code_version") != current_code:
                    mf.set_status(entry, "stale",
                                  f"analysed with {entry.get('code_version')}, "
                                  f"current code is {current_code}")
                elif old_status == "done" and entry.get("settings_sha256") and \
                        entry["settings_sha256"] != settings_mod.fingerprint(settings_mod.resolve(
                            method, sample, entry["measurement_id"])[0]):
                    mf.set_status(entry, "stale", "analysis settings changed since the last run")
                elif (old_status == "failed" and entry.get("code_version") != current_code
                      and entry["attempts"] < max_attempts):
                    mf.set_status(entry, "pending", "code changed since failure; retrying")
                if entry.get("status") != old_status:
                    changes.append({"entry_id": eid, "relative_path": relative,
                                    "from": old_status, "to": entry["status"],
                                    "reason": entry.get("status_reason")})
                counts[entry["status"]] += 1

        for eid, entry in entries.items():
            if eid not in seen and entry.get("status") != "missing" and any(
                    entry["raw_root"] == root["id"] for root in config["raw_roots"]):
                # A file inside its settle window is not missing, just not hashed yet.
                root = next(r for r in config["raw_roots"] if r["id"] == entry["raw_root"])
                if (Path(root["path"]) / entry["relative_path"]).exists():
                    continue
                old = entry.get("status")
                mf.set_status(entry, "missing", "file no longer under the raw root")
                changes.append({"entry_id": eid, "relative_path": entry["relative_path"],
                                "from": old, "to": "missing", "reason": entry["status_reason"]})
                counts["missing"] += 1

    return {"code_version": current_code, "counts": dict(sorted(counts.items())),
            "changes": changes,
            "raw_roots": [{"id": r["id"], "path": r["path"]} for r in config["raw_roots"]]}
