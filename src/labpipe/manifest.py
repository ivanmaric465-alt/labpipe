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

A done entry also carries `settings_sha256` (what the analysis depends on), `plot_sha256`
(the plot settings its figures were drawn with), `fingerprint_version` (2; entries without it
hold the old single fingerprint and are converted by the next scan, settings.compare_entry),
`plot_outdated` (the plot settings changed: `labpipe replot`, status stays done) and, if a
replot could not be done, `replot_problem`.

An entry of a method with `companions` in methods.yaml (the SEM .txt, the images an EDS report
links) carries `companions` ({path relative to the raw root: sha256}, as of the last scan) and,
once analysed, `analysed_companions` (the same, as of that analysis). When they differ the scanner
puts a done or stale entry back to pending; an entry analysed before this adopts the current
ones, or is compared with the `companions` of its provenance.json.

`output_dir` is chosen once, at an entry's first analysis (`choose_output_dir`), and kept: the
readable `<measurement_id>`, or `<measurement_id>~<6 characters of the entry id>` when another
entry holds that folder. A folder whose provenance.json names another entry is never written to
(`other_owner`); `shared_output_dirs` finds projects made before that rule.

Writes are atomic and serialised with an exclusive lock on logs/.manifest.mutex
(labpipe.locking: fcntl on Linux/macOS, msvcrt on Windows).
"""

from __future__ import annotations

import contextlib
import json
from collections import defaultdict
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


def other_owner(folder: Path, entry: dict[str, Any]) -> str | None:
    """The raw file (relative path) whose analysis is in `folder` if that is not `entry`, else None.

    Decided by the folder's provenance.json: its `entry_id`, or for older records the input's
    relative path (the raw root's name can have been changed since). The same input content
    (sha256) is never another owner: the file was moved or renamed in the data folder (the
    entry of its old place is `missing`), and its analysis belongs to the file wherever it is
    now. No provenance: no owner."""
    try:
        record = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
        given = (record.get("input") or {}).get("relative_path")
        if entry.get("sha256") and (record.get("input") or {}).get("sha256") == entry["sha256"]:
            same = True
        elif record.get("entry_id"):
            same = record["entry_id"] == entry["entry_id"]
        else:
            same = given is None or given == entry["relative_path"]
    except (OSError, ValueError, AttributeError):
        return None
    return None if same else str(given or record.get("entry_id"))


def _held(data: dict[str, Any], entry: dict[str, Any]) -> set[str]:
    return {e["output_dir"].lower() for e in data["entries"].values()
            if e is not entry and e.get("output_dir")}


def _moved_from(data: dict[str, Any], entry: dict[str, Any], base: Path) -> dict | None:
    """The `missing` entry of the same method and sample with the same content (sha256) that
    holds a folder under `base`: this file at its old place (moved or renamed). None if there is
    none, or several (then nothing is guessed)."""
    if not entry.get("sha256"):
        return None
    found = [e for e in data["entries"].values()
             if e is not entry and e.get("status") == "missing" and e.get("output_dir")
             and e.get("method") == entry["method"] and e.get("sha256") == entry["sha256"]
             and (PROJECT_ROOT / e["output_dir"]).parent == base]
    return found[0] if len(found) == 1 else None


def choose_output_dir(config: dict, data: dict[str, Any], entry: dict[str, Any]) -> str:
    """The entry's output folder (relative to the project, posix), kept once chosen.

    analysis/<sample>/<method>/<measurement_id>, or with ~<6..10 characters of the entry id's
    hash> added when another entry holds that folder (names compared without case: Windows,
    macOS) or its provenance.json names another entry. The same inputs give the same name. A
    file moved or renamed in the data folder (a `missing` entry with the same content) takes
    its folder along instead (`_moved_from`)."""
    base = PROJECT_ROOT / config["analysis_dir"] / entry["sample_id"] / entry["method"]
    stored = entry.get("output_dir")
    if stored and (PROJECT_ROOT / stored).parent == base:
        return stored
    moved = _moved_from(data, entry, base)
    if moved is not None:            # the same file at a new place: it takes its folder along
        folder = moved.pop("output_dir")
        moved["output_moved_to"] = entry["entry_id"]
        return folder
    held, digest = _held(data, entry), entry["entry_id"].split("-", 1)[-1]
    names = [entry["measurement_id"]] + [f"{entry['measurement_id']}~{digest[:n]}"
                                         for n in range(6, max(len(digest), 6) + 1)]
    for name in names:
        rel = (base / name).relative_to(PROJECT_ROOT).as_posix()
        if rel.lower() not in held and other_owner(base / name, entry) is None:
            return rel
    raise RuntimeError(f"no free output folder for {entry['entry_id']} under {base}")


def shared_output_dirs(config: dict, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Entries that share an output folder (projects from before one folder per entry).

    One row per folder: `keeps` (the entry whose provenance is in the folder; else the one
    analysed last) and `others` (entry ids). Changes nothing."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in data["entries"].values():
        if entry.get("output_dir"):
            groups[entry["output_dir"].lower()].append(entry)
    rows = []
    for members in groups.values():
        if len(members) < 2:
            continue
        folder = PROJECT_ROOT / members[0]["output_dir"]
        mine = [e for e in members if (folder / "provenance.json").is_file()
                and other_owner(folder, e) is None]
        keeper = max(mine or members, key=lambda e: (e.get("status") != "missing",
                                                     e.get("last_processed_utc") or "",
                                                     e["entry_id"]))
        rows.append({"output_dir": members[0]["output_dir"], "keeps": keeper["entry_id"],
                     "others": sorted(e["entry_id"] for e in members if e is not keeper)})
    return sorted(rows, key=lambda row: row["output_dir"])


def separate_shared_output_dirs(config: dict, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Give every entry but the keeper of a shared folder its own (nothing is deleted).

    An entry that was analysed, stale or failed goes back to pending ("shared an output folder
    with <path>; analysed again into its own folder"); its new folder is chosen now. Returns
    the changes made (as `labpipe scan` lists them)."""
    changes = []
    for row in shared_output_dirs(config, data):
        keeper = data["entries"][row["keeps"]]
        for eid in row["others"]:
            entry = data["entries"][eid]
            entry.pop("output_dir")
            if entry.get("status") in ("done", "stale", "failed"):
                old = entry["status"]
                set_status(entry, "pending", f"shared an output folder with "
                           f"{keeper['relative_path']}; analysed again into its own folder")
                changes.append({"entry_id": eid, "relative_path": entry["relative_path"],
                                "from": old, "to": "pending", "reason": entry["status_reason"]})
            if entry.get("sample_id") and entry.get("status") != "missing":
                entry["output_dir"] = choose_output_dir(config, data, entry)
    return changes
