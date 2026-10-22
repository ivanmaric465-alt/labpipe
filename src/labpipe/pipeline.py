"""Analyse manifest entries; maintain index.json files and the review queue.

Output layout: analysis/<sample_id>/<method>/<folder>/{results.json, plot.png, plot.pdf,
provenance.json, notes.md}. <folder> is the entry's own, chosen at its first analysis and kept in
the manifest (manifest.choose_output_dir): the measurement_id, plus ~<6 characters of the entry id
when another entry holds that name. A folder whose provenance.json names another entry is
refused. Each run writes into a staging folder next to the final one and is renamed into place
only when complete. Earlier outputs are moved to analysis/<sample_id>/<method>/_superseded/
<folder>/<stamp>/, failed attempts to .../_failed/<folder>/<stamp>/. Nothing is deleted.
`replot_entry` redraws the figures of a done entry (old ones to .../_superseded/<folder>/<stamp>-plot/).
"""

from __future__ import annotations

import importlib
import json
import os
import traceback
from pathlib import Path
from typing import Any

from labpipe import manifest as mf
from labpipe import versions as versions_mod
from labpipe.provenance import code_version, sha256_file, utc_now
from labpipe.registry import load_methods
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, atomic_write_text

RUNNABLE = {"pending"}


def _stamp() -> str:
    return utc_now().replace(":", "").replace("-", "")


def _free(target: Path) -> Path:
    """`target`, or target-2, -3, … when it exists (two reruns within one second share a stamp)."""
    n, free = 1, target
    while free.exists():
        n += 1
        free = target.with_name(f"{target.name}-{n}")
    return free


def _move(source: Path, target: Path, config: dict) -> None:
    assert_pipeline_writable(source, config)
    assert_pipeline_writable(target, config)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, target)


def output_dir_for(config: dict, entry: dict[str, Any]) -> Path:
    """The entry's output folder: the one kept in the manifest (chosen at its first analysis)."""
    return PROJECT_ROOT / entry["output_dir"]


def _refuse_foreign(final: Path, entry: dict[str, Any]) -> None:
    owner = mf.other_owner(final, entry)
    if owner:
        raise RuntimeError(f"output folder {final.relative_to(PROJECT_ROOT).as_posix()} holds "
                           f"the analysis of {owner}; nothing was changed there")


def analyze_entry(config: dict, eid: str, *, rerun: bool = False) -> dict[str, Any]:
    """Analyse one entry. rerun=True also redoes done/stale/failed entries (after steering)."""
    methods = load_methods()
    with mf.locked(config) as data:
        entry = data["entries"].get(eid)
        if entry is None:
            raise KeyError(f"unknown entry {eid}")
        allowed = RUNNABLE | ({"stale", "done", "failed"} if rerun else set())
        if entry["status"] not in allowed:
            return {"entry_id": eid, "status": "skipped",
                    "reason": f"status is {entry['status']}, not {sorted(allowed)}"}
        entry["output_dir"] = mf.choose_output_dir(config, data, entry)   # once; kept
        entry = json.loads(json.dumps(entry))  # snapshot outside the lock
    spec = methods[entry["method"]]
    root = next(r for r in config["raw_roots"] if r["id"] == entry["raw_root"])
    input_path = Path(root["path"]) / entry["relative_path"]
    final = output_dir_for(config, entry)
    method_dir = final.parent
    stamp = _stamp()
    staging = method_dir / f".staging-{final.name}-{stamp}"
    assert_pipeline_writable(staging, config)

    status, reason, summary = "failed", None, None
    versions = versions_mod.current(entry["method"])   # of the code this run uses
    try:
        _refuse_foreign(final, entry)
        digest = sha256_file(input_path)
        if digest != entry["sha256"]:
            raise RuntimeError("file changed since the last scan; run `labpipe scan` first")
        module = importlib.import_module(spec["module"])
        summary = module.run(input_path, staging, context={
            "sample_id": entry["sample_id"], "measurement_id": entry["measurement_id"],
            "relative_path": entry["relative_path"], "raw_root": root["id"],
            "method": entry["method"], "entry_id": eid, "output_dir": final.relative_to(PROJECT_ROOT).as_posix(),
        }, pipeline=True)
        _refuse_foreign(final, entry)
        status = "done"
    except Exception as exc:  # recorded, never retried endlessly
        reason = f"{type(exc).__name__}: {exc}"
        known = getattr(module, "AnalysisError", ()) if "module" in locals() else ()
        if not (known and isinstance(exc, known)):
            log = (PROJECT_ROOT / config["logs_dir"] / "errors"
                   / f"{eid}-{stamp}.txt")
            atomic_write_text(log, traceback.format_exc(), config)
            reason += f" (traceback: {log.relative_to(PROJECT_ROOT)})"

    if status == "done":
        if final.exists() and any(final.iterdir()):
            _move(final, _free(method_dir / "_superseded" / final.name / stamp),
                  config)
        _move(staging, final, config)
    elif staging.exists():
        _move(staging, _free(method_dir / "_failed" / final.name / stamp), config)

    with mf.locked(config) as data:
        stored = data["entries"][eid]
        stored["attempts"] = stored.get("attempts", 0) + 1
        stored["code_version"] = code_version()
        stored.update(versions)
        stored["last_processed_utc"] = utc_now()
        if status == "done":
            stored["output_dir"] = final.relative_to(PROJECT_ROOT).as_posix()
            stored["results_sha256"] = summary["outputs"]["results.json"]
            stored["settings_sha256"] = summary["settings_sha256"]
            stored["plot_sha256"] = summary.get("plot_sha256")
            stored["fingerprint_version"] = summary.get("fingerprint_version")
            stored["plot_outdated"] = False
            for key in ("plot_outdated_reason", "plot_sha256_wanted", "replot_problem"):
                stored.pop(key, None)
            stored["review"] = summary["status"] == "review"
            stored["review_codes"] = sorted({c["code"] for c in summary["checks"]})
            mf.set_status(stored, "done", "analysis complete" + (
                " (flagged for human review)" if stored["review"] else ""))
        else:
            mf.set_status(stored, "failed", reason)
        write_index(config, data, stored["sample_id"], stored["method"])
    return {"entry_id": eid, "status": status, "reason": reason,
            "output_dir": final.relative_to(PROJECT_ROOT).as_posix() if status == "done" else None,
            "review": summary["status"] == "review" if summary else None,
            "review_checks": summary["checks"] if summary else None}


def _input_for(config: dict, entry: dict[str, Any]) -> tuple[dict, Path]:
    root = next(r for r in config["raw_roots"] if r["id"] == entry["raw_root"])
    return root, Path(root["path"]) / entry["relative_path"]


def _only_figures_differ(old: bytes, new: bytes) -> bool:
    """True if two results.json differ in nothing but the list of `figures` (the old one is
    exactly as the code writes it, so a hand-edited file never passes)."""
    try:
        before, after = json.loads(old), json.loads(new)
    except ValueError:
        return False
    written = (json.dumps(before, indent=1, ensure_ascii=False, allow_nan=False)
               + "\n").encode()
    before.pop("figures", None)
    after.pop("figures", None)
    return written == old and before == after


def replot_entry(config: dict, eid: str) -> dict[str, Any]:
    """Redraw the figures of a done entry; its analysis is not changed.

    The analysis code runs again into a staging folder (the plotting is part of it). Only if
    that gives the same results.json (byte for byte; for the list of `figures` alone a
    different list is accepted) are the figures replaced: the old ones and the old
    provenance.json move to _superseded/<folder>/<stamp>-plot/, notes.md stays.
    Otherwise nothing is replaced: the new run is kept in _failed/ and the entry records a
    `replot_problem` for a human (its status stays done: the earlier outputs are still good).
    """
    methods = load_methods()
    with mf.locked(config) as data:
        entry = data["entries"].get(eid)
        if entry is None:
            raise KeyError(f"unknown entry {eid}")
        if entry["status"] != "done" or not entry.get("output_dir"):
            return {"entry_id": eid, "status": "skipped",
                    "reason": f"status is {entry['status']}, not done"}
        entry = json.loads(json.dumps(entry))
    final = output_dir_for(config, entry)
    method_dir = final.parent
    if not (final / "results.json").is_file():
        return {"entry_id": eid, "status": "skipped", "reason": "no results.json to compare with"}
    spec = methods[entry["method"]]
    root, input_path = _input_for(config, entry)
    stamp = _stamp()
    mid = final.name
    staging = method_dir / f".staging-{mid}-{stamp}-plot"
    assert_pipeline_writable(staging, config)

    problem, summary = None, None
    try:
        _refuse_foreign(final, entry)
        if sha256_file(input_path) != entry["sha256"]:
            raise RuntimeError("file changed since the last scan; run `labpipe scan` first")
        module = importlib.import_module(spec["module"])
        summary = module.run(input_path, staging, context={
            "sample_id": entry["sample_id"], "measurement_id": entry["measurement_id"],
            "relative_path": entry["relative_path"], "raw_root": root["id"],
            "method": entry["method"], "entry_id": eid, "output_dir": final.relative_to(PROJECT_ROOT).as_posix(),
        }, pipeline=True)
        old_results = (final / "results.json").read_bytes()
        new_results = (staging / "results.json").read_bytes()
        if new_results != old_results and not _only_figures_differ(old_results, new_results):
            problem = ("reproducibility problem: the analysis no longer gives the same "
                       "results.json (code, settings or environment changed?); nothing was "
                       "replaced, the new run is kept for a human to compare")
    except Exception as exc:
        problem = f"{type(exc).__name__}: {exc}"

    if problem:
        kept = None
        if staging.exists():
            kept = method_dir / "_failed" / mid / f"{stamp}-replot"
            _move(staging, kept, config)
        with mf.locked(config) as data:
            stored = data["entries"][eid]
            stored["replot_problem"] = {
                "utc": utc_now(), "reason": problem, "code_version": code_version(),
                "for_plot_sha256": entry.get("plot_sha256_wanted"),
                "kept_in": kept.relative_to(PROJECT_ROOT).as_posix() if kept else None}
            stored.setdefault("events", []).append(
                {"utc": utc_now(), "from": "done", "to": "done",
                 "reason": f"replot failed: {problem}"[:300]})
            stored["events"] = stored["events"][-30:]
        return {"entry_id": eid, "status": "failed", "reason": problem,
                "kept_in": kept.relative_to(PROJECT_ROOT).as_posix() if kept else None}

    # Old figures (everything the old provenance lists but results.json) and the old
    # provenance.json go aside; notes.md and anything else in the folder stay.
    old_files = {"provenance.json"}
    try:
        old_files |= set(json.loads((final / "provenance.json").read_text(
            encoding="utf-8"))["outputs"])
    except (OSError, ValueError, KeyError):
        pass
    old_files |= {p.name for p in staging.iterdir()}
    old_files.discard("notes.md")
    results_same = new_results == old_results
    if results_same:
        old_files.discard("results.json")
        (staging / "results.json").unlink()   # byte-identical copy of the kept file
    aside = _free(method_dir / "_superseded" / mid / f"{stamp}-plot")
    for name in sorted(old_files):
        if (final / name).exists():
            _move(final / name, aside / name, config)
    for path in sorted(staging.iterdir()):
        _move(path, final / path.name, config)
    staging.rmdir()

    with mf.locked(config) as data:
        stored = data["entries"][eid]
        stored["plot_sha256"] = summary["plot_sha256"]
        stored["plot_version"] = versions_mod.current(stored["method"])["plot_version"]
        stored["plot_outdated"] = False
        for key in ("plot_outdated_reason", "plot_sha256_wanted", "replot_problem"):
            stored.pop(key, None)
        stored["last_replot_utc"] = utc_now()
        if not results_same:    # only the list of figures differs: the notes stay valid
            stored["same_analysis_sha256"] = sorted(
                {*stored.get("same_analysis_sha256", []), stored["results_sha256"]})
            stored["results_sha256"] = summary["outputs"]["results.json"]
        write_index(config, data, stored["sample_id"], stored["method"])
    return {"entry_id": eid, "status": "done", "reason": None,
            "output_dir": final.relative_to(PROJECT_ROOT).as_posix(),
            "results_changed": not results_same,
            "superseded": aside.relative_to(PROJECT_ROOT).as_posix() if aside.exists() else None}


def replot(config: dict, *, entry: str | None = None, method: str | None = None,
           everything: bool = False) -> dict[str, Any]:
    """Redraw figures: one entry, one method, all done entries, or (default) the entries
    whose plot settings changed since they were drawn."""
    rows = [e for e in mf.load(config)["entries"].values() if e.get("status") == "done"]
    if entry:
        rows = [e for e in rows if e["entry_id"] == entry]
        if not rows:
            return {"error": f"no done entry {entry}", "results": []}
    elif method:
        rows = [e for e in rows if e["method"] == method]
    elif not everything:
        rows = [e for e in rows if e.get("plot_outdated")]
    results = [replot_entry(config, e["entry_id"]) for e in sorted(rows, key=lambda e: e["entry_id"])]
    return {"replotted": [r["entry_id"] for r in results if r["status"] == "done"],
            "failed": [r["entry_id"] for r in results if r["status"] == "failed"],
            "skipped": [r["entry_id"] for r in results if r["status"] == "skipped"],
            "results": results}


def write_index(config: dict, data: dict[str, Any], sample_id: str | None, method: str) -> None:
    """analysis/<sample>/<method>/index.json: every measurement of that sample and method."""
    if not sample_id:
        return
    rows = [{key: entry.get(key) for key in (
                "entry_id", "measurement_id", "relative_path", "sha256", "status",
                "status_reason", "review", "review_codes", "output_dir", "code_version",
                "last_processed_utc")}
            for entry in data["entries"].values()
            if entry.get("sample_id") == sample_id and entry.get("method") == method
            and entry.get("status") not in ("duplicate", "ignored")]
    rows.sort(key=lambda row: row["measurement_id"])
    path = PROJECT_ROOT / config["analysis_dir"] / sample_id / method / "index.json"
    atomic_write_text(path, json.dumps({"schema": "labpipe.index/1", "sample_id": sample_id,
                                        "method": method, "measurements": rows},
                                       indent=1, ensure_ascii=False) + "\n", config)


def review_queue(config: dict) -> dict[str, Any]:
    data = mf.load(config)
    groups: dict[str, list[dict[str, Any]]] = {"needs_review": [], "failed": [],
                                               "done_flagged": [], "stale": [],
                                               "plot_outdated": [], "plot_problem": []}
    for entry in data["entries"].values():
        status = entry.get("status")
        if status == "needs_review":
            groups["needs_review"].append(entry)
        elif status == "failed":
            groups["failed"].append(entry)
        elif status == "stale":
            groups["stale"].append(entry)
        elif status == "done" and entry.get("review"):
            groups["done_flagged"].append(entry)
        if status == "done" and entry.get("replot_problem"):
            groups["plot_problem"].append(entry)
        elif status == "done" and entry.get("plot_outdated"):
            groups["plot_outdated"].append(entry)
    lines = [f"# Review queue ({utc_now()})", ""]
    unrecognised = sorted(data.get("unrecognised", {}).values(),
                          key=lambda r: (r["raw_root"], r["relative_path"]))
    lines.append(f"## Not recognised by any method ({len(unrecognised)})")
    lines.append("Tell the agent what these are (it can add them to a method or create one), "
                 "or list them in config/ignore.yaml to leave them alone. Your own analyses "
                 "(a figure, a table): tell the agent \"include my own analyses in <folder>\" "
                 "(`labpipe methods import`).")
    for record in unrecognised:
        lines.append(f"- `{record['relative_path']}` ({record['raw_root']}, first seen "
                     f"{record['first_seen_utc']})")
    lines.append("")
    titles = {
        "needs_review": "Needs a human decision (answer in config/sample_ids.yaml)",
        "failed": "Failed analyses",
        "done_flagged": "Analysed, flagged for review (see notes.md / results.json checks)",
        "stale": "Analysed with an older code version (`labpipe requeue --stale` to redo)",
        "plot_outdated": "Plot out of date: plot settings changed (`labpipe replot` redraws; "
                         "automatic runs do it; the analysis is fine)",
        "plot_problem": "Plot could not be redrawn: the results would change "
                        "(a human should look; the earlier outputs are kept)",
    }
    for key, title in titles.items():
        lines.append(f"## {title} ({len(groups[key])})")
        for entry in sorted(groups[key], key=lambda e: e["relative_path"]):
            detail = entry.get("status_reason") or ""
            if key == "done_flagged":
                detail = ", ".join(entry.get("review_codes") or [])
            elif key == "plot_outdated":
                detail = entry.get("plot_outdated_reason") or ""
            elif key == "plot_problem":
                detail = entry["replot_problem"]["reason"][:200]
            location = entry.get("output_dir") or ""
            lines.append(f"- `{entry['relative_path']}` ({entry['entry_id']}) {location} — {detail}")
        lines.append("")
    atomic_write_text(PROJECT_ROOT / config["logs_dir"] / "review_queue.md", "\n".join(lines),
                      config)
    return {"not_recognised": len(unrecognised)} | {key: len(value) for key, value in groups.items()}
