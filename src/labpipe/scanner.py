"""Scan raw roots and bring manifest.json up to date. Idempotent.

* unchanged file (same sha256): left alone;
* changed file: previous state moved to the entry's history, status pending;
* done with an older code version or different analysis settings: marked stale
  (not reprocessed automatically); settings that only concern the plots (`plot:` blocks,
  plot style) leave it done and mark it `plot_outdated` (`labpipe replot` redraws);
* failed: back to pending only if the code version changed and the attempt
  budget for this file hash is not used up;
* needs_review: back to pending once a human override resolves it;
* files modified within settle_minutes are skipped (a sync client or the instrument PC may still be writing);
* entries whose file disappeared: status missing (outputs are kept);
* a file byte-identical to another file of the same method (e.g. "X - Copy.htm")
  is recorded as duplicate of the one with the shortest path, not analysed;
* baseline=True (setup, "only new files from now on"): files seen for the first
  time are recorded as baseline, not analysed; they become pending when their
  content changes or on `labpipe requeue --baseline | --entry ID`.
* files no method recognises are recorded once (path, size, sha256) in the
  manifest's `unrecognised` section and returned as `unrecognised_new` when new
  or changed, so the user is told once per file and content; system files and
  files in config/ignore.yaml are left out (registry.skip_reason).

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
from labpipe.naming import DEFAULT_SEPARATORS, derive_sample_id, entry_id, measurement_id
from labpipe.provenance import code_version, sha256_file, utc_now
from labpipe.registry import classify, load_methods, skip_reason, user_ignore
from labpipe.safety import PROJECT_ROOT


def _overrides(config: dict) -> dict[str, str]:
    path = PROJECT_ROOT / config["sample_id"]["overrides_file"]
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(data.get("overrides") or {})


ACTIVE = {"pending", "done", "stale", "failed", "needs_review", "baseline"}


def _is_content_duplicate(entry: dict[str, Any]) -> bool:
    return entry.get("status") == "duplicate" and str(entry.get("status_reason", "")).startswith(
        "identical content")


def _content_duplicates(entries: dict[str, dict[str, Any]], changes: list, counts: Counter) -> None:
    """Same method + same sha256: keep the shortest path, mark the others duplicate."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries.values():
        if entry.get("sha256") and (entry.get("status") in ACTIVE or _is_content_duplicate(entry)):
            groups.setdefault((entry["method"], entry["sha256"]), []).append(entry)

    def move(entry: dict[str, Any], status: str, reason: str) -> None:
        old = entry.get("status")
        mf.set_status(entry, status, reason)
        if old != status:
            counts[old] -= 1
            counts[status] += 1
            changes.append({"entry_id": entry["entry_id"], "relative_path": entry["relative_path"],
                            "from": old, "to": status, "reason": reason})

    for members in groups.values():
        members.sort(key=lambda e: (len(e["relative_path"]), e["relative_path"]))
        keeper, *twins = members
        if _is_content_duplicate(keeper):
            keeper.pop("duplicate_of", None)
            move(keeper, "pending", "the identical file it duplicated is gone")
        for twin in twins:
            if not _is_content_duplicate(twin) or twin.get("duplicate_of") != keeper["entry_id"]:
                twin["duplicate_of"] = keeper["entry_id"]
                move(twin, "duplicate", f"identical content to {keeper['relative_path']}")


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
    separators = config["sample_id"].get("separators", DEFAULT_SEPARATORS)
    settle_seconds = 60 * config["settle_minutes"]
    max_attempts = config["max_attempts_per_hash"]
    current_code = code_version()
    now = time.time()
    counts: Counter[str] = Counter()
    changes: list[dict[str, Any]] = []

    ignored = user_ignore()
    new_unrecognised: list[str] = []
    with mf.locked(config) as data:
        entries = data["entries"]
        unrecognised = data.setdefault("unrecognised", {})
        seen: set[str] = set()
        seen_unrecognised: set[str] = set()
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
                skip = skip_reason(relative, config, ignored)
                if skip == "system file":
                    counts["system_file"] += 1
                    continue
                method, kind = classify(relative, methods, root=root_path)
                if method is None and skip:
                    counts["ignored_by_you"] += 1
                    continue
                if skip:
                    kind = "ignore"
                stat = full.stat()
                key = f"{root['id']}:{relative}"
                if now - stat.st_mtime < settle_seconds:
                    counts["settling"] += 1
                    if method is None and key in unrecognised:
                        seen_unrecognised.add(key)
                    continue
                if method is None:
                    seen_unrecognised.add(key)
                    counts["unrecognised"] += 1
                    mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
                    record = unrecognised.get(key)
                    if record and (record["size_bytes"], record["mtime_utc"]) == (stat.st_size, mtime):
                        continue
                    digest = sha256_file(full)
                    if (record is None or record["sha256"] != digest) and not baseline:
                        new_unrecognised.append(relative)
                    unrecognised[key] = {"raw_root": root["id"], "relative_path": relative,
                                         "size_bytes": stat.st_size, "mtime_utc": mtime,
                                         "sha256": digest,
                                         "first_seen_utc": (record or {}).get("first_seen_utc")
                                         or utc_now()}
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
                                                           separators=separators,
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
                    mf.set_status(entry, "ignored", skip or "matches an ignore pattern in methods.yaml")
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
                elif old_status == "done" and entry.get("settings_sha256"):
                    resolved = settings_mod.resolve(method, sample, entry["measurement_id"])[0]
                    state = settings_mod.compare_entry(entry, resolved)
                    if not state["analysis_current"]:
                        mf.set_status(entry, "stale", "analysis settings changed since the "
                                                      "last run")
                    else:
                        entry.update(state["adopt"])
                        if not state["plot_current"]:
                            entry["plot_outdated"] = True
                            entry["plot_sha256_wanted"] = settings_mod.plot_fingerprint(resolved)
                            entry["plot_outdated_reason"] = ("plot settings changed since the "
                                                             "figures were drawn")
                        else:
                            entry["plot_outdated"] = False
                            entry.pop("plot_outdated_reason", None)
                            entry.pop("plot_sha256_wanted", None)
                elif (old_status == "failed" and entry.get("code_version") != current_code
                      and entry["attempts"] < max_attempts):
                    mf.set_status(entry, "pending", "code changed since failure; retrying")
                if entry.get("status") != old_status:
                    changes.append({"entry_id": eid, "relative_path": relative,
                                    "from": old_status, "to": entry["status"],
                                    "reason": entry.get("status_reason")})
                counts[entry["status"]] += 1

        _content_duplicates(entries, changes, counts)
        for key in [k for k in unrecognised if k not in seen_unrecognised]:
            del unrecognised[key]       # gone, now recognised, or ignored
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

    return {"code_version": current_code,
            "counts": dict(sorted((k, n) for k, n in counts.items() if n)),
            "plot_out_of_date": sum(1 for e in entries.values()
                                    if e.get("status") == "done" and e.get("plot_outdated")),
            "changes": changes,
            "unrecognised_new": new_unrecognised,
            "raw_roots": [{"id": r["id"], "path": r["path"]} for r in config["raw_roots"]]}
