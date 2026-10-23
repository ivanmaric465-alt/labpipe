"""Scan raw roots and bring manifest.json up to date. Idempotent.

* unchanged file (same sha256): left alone;
* changed file: previous state moved to the entry's history, status pending;
* done with another analysis version of its method (src/labpipe/versions.py) or different
  analysis settings: marked stale (not reprocessed automatically); a git commit that does not
  change the method's calculation changes nothing. Settings that only concern the plots
  (`plot:` blocks, plot style) or another plot version leave it done and mark it
  `plot_outdated` (`labpipe replot` redraws);
* a method with `companions` in methods.yaml (SEM: the JEOL .txt; EDS: the images the report
  links): the entry records their hashes (`companions`, by path relative to the raw root). A
  done or stale entry whose companions differ from the ones it was analysed with
  (`analysed_companions`) goes back to pending, naming the file ("companion S-1 a001.txt
  changed", "appeared", "is gone"), before the stale and settings checks, as a changed main file
  does. An entry analysed before this adopts the current ones when its provenance.json lists
  the same files and hashes (or lists none), and goes to pending when it does not;
* done and analysed before versions existed: the current versions are adopted if the
  entry's code_version is the current, clean commit; otherwise it is marked stale;
* stale, but its method's analysis version and its analysis settings are the current ones
  again (a rollback, a setting changed back): done again;
* failed: back to pending only if the method's analysis version changed and the attempt
  budget for this file hash is not used up;
* needs_review: back to pending once a human override resolves it;
* files modified within settle_minutes are skipped (a sync client or the instrument PC may still be writing);
* entries whose file disappeared: status missing (outputs are kept);
* entries that share an output folder (projects made before one folder per entry): the one whose
  provenance is in the folder keeps it, the others get their own folder and go back to pending
  (manifest.separate_shared_output_dirs; nothing is deleted);
* an imported kind (your own analyses, `kind: imported`): the files of one stem in one folder
  are one measurement. The primary file (table, then image, then text, then PDF) is the entry;
  the others are recorded as duplicate ("part of the measurement"), and when one of them is new
  or changed its primary is analysed again. The sample ID comes from the file name, or with
  `sample_from: folder` from the name of its folder;
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

import json
import os
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from labpipe import manifest as mf
from labpipe import migrations
from labpipe import settings as settings_mod
from labpipe import versions as versions_mod
from labpipe.naming import DEFAULT_SEPARATORS, derive_sample_id, entry_id, measurement_id
from labpipe.provenance import code_version, sha256_file, utc_now
from labpipe.registry import classify, input_companions, load_methods, skip_reason, user_ignore
from labpipe.safety import PROJECT_ROOT


def _overrides(config: dict) -> dict[str, str]:
    path = PROJECT_ROOT / config["sample_id"]["overrides_file"]
    if not path.exists():
        return {}
    data = migrations.upgraded(path, yaml.safe_load(path.read_text(encoding="utf-8")) or {},
                               PROJECT_ROOT)
    return dict(data.get("overrides") or {})


ACTIVE = {"pending", "done", "stale", "failed", "needs_review", "baseline"}


def _is_content_duplicate(entry: dict[str, Any]) -> bool:
    return entry.get("status") == "duplicate" and str(entry.get("status_reason", "")).startswith(
        "identical content")


def _content_duplicates(entries: dict[str, dict[str, Any]], changes: list, counts: Counter,
                        per_sample: frozenset[str] = frozenset()) -> None:
    """Same method + same sha256: keep the shortest path, mark the others duplicate. For the
    methods in `per_sample` (imported kinds: your own analyses) only within one sample: the
    same file under two samples is two measurements (user decision 2026-10-22)."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for entry in entries.values():
        if entry.get("sha256") and (entry.get("status") in ACTIVE or _is_content_duplicate(entry)):
            sample = entry.get("sample_id") if entry["method"] in per_sample else None
            groups.setdefault((entry["method"], entry["sha256"], sample), []).append(entry)

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


def _companion_changes(then: dict[str, str], now: dict[str, str]) -> list[str]:
    """What differs between two {path: sha256} sets of companions, as short phrases."""
    name = lambda path: PurePosixPath(path).name      # noqa: E731
    return ([f"{name(p)} changed" for p in sorted(then.keys() & now.keys()) if then[p] != now[p]]
            + [f"{name(p)} appeared" for p in sorted(now.keys() - then.keys())]
            + [f"{name(p)} is gone" for p in sorted(then.keys() - now.keys())])


def _companion_reason(entry: dict[str, Any], current: dict[str, str] | None) -> str | None:
    """Why a done or stale entry is out of date because a companion file changed, appeared or
    disappeared (None: it is not). An entry analysed before companions were tracked has no
    `analysed_companions`: the ones in its provenance.json are compared (by file name), and it
    adopts the current ones when they agree or when the provenance lists none."""
    if current is None:
        return None
    recorded = entry.get("analysed_companions")
    if recorded is None:
        try:
            provenance = json.loads((PROJECT_ROOT / entry["output_dir"] / "provenance.json")
                                    .read_text(encoding="utf-8"))
            listed = {item["file"]: item["sha256"] for item in provenance["companions"]}
        except (OSError, ValueError, KeyError, TypeError):
            listed = None       # nothing to compare with
        if listed is not None:
            changes = _companion_changes(
                listed, {PurePosixPath(path).name: sha for path, sha in current.items()})
            if changes:
                return _companion_text(changes)
        entry["analysed_companions"] = dict(current)
        return None
    changes = _companion_changes(recorded, current)
    return _companion_text(changes) if changes else None


def _companion_text(changes: list[str]) -> str:
    shown = [f"companion {change}" for change in changes[:3]]
    return ", ".join(shown) + (f" and {len(changes) - 3} more" if len(changes) > 3 else "")


def _stale_reason(entry: dict[str, Any], versions: dict[str, Any], current_code: str) -> str | None:
    """Why a done entry is out of date because of the code (None: it is not). An entry from
    before per-method versions adopts the current ones if it was analysed with exactly the
    current clean commit; otherwise nobody knows whether its calculation changed."""
    now, then = versions["analysis_version"], entry.get("analysis_version")
    if then is None:
        if entry.get("code_version") == current_code and not current_code.endswith("+dirty"):
            entry.update(analysis_version=now, plot_version=versions["plot_version"])
            return None
        return ("analysed before per-method versions; redo to be sure "
                "(`labpipe requeue --stale`)")
    if then != now:
        return f"the {entry['method']} calculation changed (version {then} → {now})"
    return None


def _current_again(entry: dict[str, Any], versions: dict[str, Any], sample: str) -> bool:
    """A stale entry whose calculation and analysis settings are the current ones again (e.g.
    after `labpipe update --to` an older release, or a setting changed back): its results are
    valid as they are. One from before versions existed stays stale."""
    if entry.get("analysis_version") != versions["analysis_version"]:
        return False
    if not entry.get("settings_sha256"):
        return True
    resolved = settings_mod.resolve(entry["method"], sample, entry["measurement_id"])[0]
    return settings_mod.compare_entry(entry, resolved)["analysis_current"]


def _changed_since_failure(entry: dict[str, Any], versions: dict[str, Any],
                           current_code: str) -> bool:
    """A failed entry is retried when its method's calculation changed. One from before
    versions existed is retried when the git commit changed (the old rule), once."""
    then = entry.get("analysis_version")
    if then is None:
        return entry.get("code_version") != current_code
    return then != versions["analysis_version"]


def sample_id_for(relative: str, spec: dict[str, Any], config: dict, overrides: dict[str, str]):
    """(sample, source, problem): the file name rule, or for `sample_from: folder` (imported
    kinds) the same rule on the name of the file's folder; an override of the file wins."""
    pattern, separators = config["sample_id"]["pattern"], config["sample_id"].get(
        "separators", DEFAULT_SEPARATORS)
    folder = PurePosixPath(relative).parent.name
    if spec.get("sample_from") != "folder" or relative in overrides:
        return derive_sample_id(relative, pattern=pattern, separators=separators,
                                overrides=overrides)
    if not folder:
        return None, "folder name rule", "the file is not in a folder: no sample ID"
    sample, _, problem = derive_sample_id(f"{folder}.folder", pattern=pattern,
                                          separators=separators, overrides={})
    return sample, "folder name rule", problem and problem.replace(".folder", "")


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
    settle_seconds = 60 * config["settle_minutes"]
    max_attempts = config["max_attempts_per_hash"]
    current_code = code_version()
    versions = {name: versions_mod.current(name) for name in methods}
    now = time.time()
    counts: Counter[str] = Counter()
    changes: list[dict[str, Any]] = []

    ignored = user_ignore()
    new_unrecognised: list[str] = []
    touched: set[str] = set()       # primaries of imported measurements with a new or changed part
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
            claims = None       # imported kinds: what each file belongs to, and its measurement
            if any(spec.get("kind") == "imported" for spec in methods.values()):
                claims = {relative: classify(relative, methods, root=root_path)
                          for _, relative in files}
                parts: dict[tuple[str, str, str], dict[str, str]] = {}   # -> {extension: path}
                for relative, (method, kind) in claims.items():
                    if method and kind == "analyse" and methods[method].get("kind") == "imported":
                        path = PurePosixPath(relative)
                        parts.setdefault((method, str(path.parent), path.stem.lower()),
                                         {})[path.suffix.lower()] = relative

            for full, relative in files:
                skip = skip_reason(relative, config, ignored)
                if skip == "system file":
                    counts["system_file"] += 1
                    continue
                method, kind = claims[relative] if claims else classify(
                    relative, methods, root=root_path)
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
                sample, source, problem = sample_id_for(relative, spec, config, overrides)
                mid = measurement_id(relative)
                if spec.get("sample_from") == "folder" and sample and not mid.startswith(sample):
                    mid = f"{sample}_{mid}"
                entry.update({"sample_id": sample, "sample_id_source": source,
                              "measurement_id": mid})
                primary = None      # the file this one is only another form or part of
                if path.suffix.lower() in spec.get("duplicate_of_primary", []) and spec.get(
                        "primary_extension") in stems.get((str(path.parent), path.stem.lower()),
                                                          set()):
                    primary = path.with_suffix(spec["primary_extension"]).as_posix()
                elif spec.get("kind") == "imported" and kind == "analyse":
                    from labpipe.methods.imported.analyze import primary_of
                    group = parts[(method, str(path.parent), path.stem.lower())]
                    if path.suffix.lower() != primary_of(group):
                        primary = group[primary_of(group)]

                if previous_hash is not None and previous_hash != digest:
                    entry["history"].append({
                        "sha256": previous_hash, "status": entry.get("status"),
                        "output_dir": entry.get("output_dir"),
                        "code_version": entry.get("code_version"), "replaced_utc": utc_now()})
                    entry["attempts"] = 0
                entry["sha256"] = digest
                current = None      # the companion files and their hashes, for an analysed entry
                if kind != "ignore" and not primary and spec.get("companions"):
                    try:
                        current = entry["companions"] = input_companions(spec, root_path, relative, (
                            settings_mod.resolve(method, sample, mid)[0] if sample else {}))
                    except OSError:
                        pass        # not readable now: the last record stays

                old_status = entry.get("status")
                if kind == "ignore":
                    mf.set_status(entry, "ignored", skip or "matches an ignore pattern in methods.yaml")
                elif primary:
                    entry["duplicate_of"] = entry_id(method, root["id"], primary)
                    mf.set_status(entry, "duplicate", f"same measurement as {primary}" + (
                        " (primary format)" if spec.get("primary_extension") else
                        " (its main file)"))
                    if spec.get("kind") == "imported" and (is_new or previous_hash != digest):
                        touched.add(entry["duplicate_of"])
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
                elif old_status in ("done", "stale") and (reason := _companion_reason(
                        entry, current)):
                    mf.set_status(entry, "pending", reason)
                elif old_status == "done" and (reason := _stale_reason(
                        entry, versions[method], current_code)):
                    mf.set_status(entry, "stale", reason)
                elif old_status == "done" and entry.get("settings_sha256"):
                    resolved = settings_mod.resolve(method, sample, entry["measurement_id"])[0]
                    state = settings_mod.compare_entry(entry, resolved)
                    if not state["analysis_current"]:
                        mf.set_status(entry, "stale", "analysis settings changed since the "
                                                      "last run")
                    else:
                        entry.update(state["adopt"])
                        plot_code = entry.get("plot_version") == versions[method]["plot_version"]
                        if not (state["plot_current"] and plot_code):
                            entry["plot_outdated"] = True
                            entry["plot_sha256_wanted"] = settings_mod.plot_fingerprint(resolved)
                            entry["plot_outdated_reason"] = (
                                "plot settings changed since the figures were drawn"
                                if not state["plot_current"] else
                                "the plot code changed since the figures were drawn")
                        else:
                            entry["plot_outdated"] = False
                            entry.pop("plot_outdated_reason", None)
                            entry.pop("plot_sha256_wanted", None)
                elif old_status == "stale" and _current_again(entry, versions[method], sample):
                    mf.set_status(entry, "done", "current again: the calculation and settings "
                                  "it was analysed with are the current ones")
                elif (old_status == "failed" and entry["attempts"] < max_attempts
                      and _changed_since_failure(entry, versions[method], current_code)):
                    mf.set_status(entry, "pending", "code changed since failure; retrying")
                if entry.get("status") != old_status:
                    changes.append({"entry_id": eid, "relative_path": relative,
                                    "from": old_status, "to": entry["status"],
                                    "reason": entry.get("status_reason")})
                counts[entry["status"]] += 1

        for eid in touched:
            primary = entries.get(eid)
            if primary and primary.get("status") in ("done", "stale"):
                old = primary["status"]
                mf.set_status(primary, "pending", "a file of this measurement is new or changed")
                changes.append({"entry_id": eid, "relative_path": primary["relative_path"],
                                "from": old, "to": "pending", "reason": primary["status_reason"]})
                counts[old] -= 1
                counts["pending"] += 1
        for key in [k for k in unrecognised if k not in seen_unrecognised]:
            del unrecognised[key]       # gone, now recognised, or ignored
        # files gone first: a moved file is then not taken for a duplicate of its old place
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
        _content_duplicates(entries, changes, counts, frozenset(
            name for name, spec in methods.items() if spec.get("kind") == "imported"))
        for change in mf.separate_shared_output_dirs(config, data):
            changes.append(change)
            counts[change["from"]] -= 1
            counts["pending"] += 1

    return {"code_version": current_code,
            "counts": dict(sorted((k, n) for k, n in counts.items() if n)),
            "plot_out_of_date": sum(1 for e in entries.values()
                                    if e.get("status") == "done" and e.get("plot_outdated")),
            "changes": changes,
            "unrecognised_new": new_unrecognised,
            "raw_roots": [{"id": r["id"], "path": r["path"]} for r in config["raw_roots"]]}
