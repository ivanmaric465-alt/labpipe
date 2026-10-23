"""Analyse manifest entries; maintain index.json files and the review queue.

Output layout: analysis/<sample_id>/<method>/<folder>/{results.json, plot.png, plot.pdf,
provenance.json, notes.md}. <folder> is the entry's own, chosen at its first analysis and kept in
the manifest (manifest.choose_output_dir): the measurement_id, plus ~<6 characters of the entry id
when another entry holds that name. A folder whose provenance.json names another entry is
refused. Each run writes into a staging folder next to the final one and is renamed into place
only when complete. Earlier outputs are moved to analysis/<sample_id>/<method>/_superseded/
<folder>/<stamp>/, failed attempts to .../_failed/<folder>/<stamp>/. Nothing is deleted.
`replot_entry` redraws the figures of a done entry (old ones to .../_superseded/<folder>/<stamp>-plot/).

Publication is one step (hardening B2):
- one lock per measurement (logs/locks/<entry_id>.lock) is held for the whole analysis and
  publication, so the watcher and a manual run never do the same one; the second returns
  "skipped" and changes nothing;
- while still in staging the method's summary is checked against the files (results.json parses,
  every listed output has its sha256, provenance.json names this entry and lists the same
  outputs, the summary has the keys the manifest needs); a failure fails the analysis;
- just before publishing, the main file and its companions are hashed again; if one changed
  while the run was going, the entry goes back to `pending` (no attempt counted);
- before the first rename a journal (logs/publishing/<entry_id>.json) names staging, final and
  the folder the old outputs go to; it is removed after the manifest is updated. A journal found
  later (`recover_publications`, at the start of every scan and of an analysis of that entry)
  means a crash: the new outputs (or what is left in staging) move to
  .../_failed/<folder>/<stamp>-interrupted/, the old outputs move back, and the entry is
  `pending` again.
"""

from __future__ import annotations

import importlib
import json
import os
import traceback
from pathlib import Path
from typing import Any

from labpipe import manifest as mf
from labpipe import settings as settings_mod
from labpipe import versions as versions_mod
from labpipe.locking import try_exclusive
from labpipe.provenance import code_version, sha256_file, utc_now
from labpipe.registry import input_companions, load_methods
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


class AnalysisRefused(RuntimeError):
    """A run that labpipe itself stops (inconsistent summary, foreign folder): no traceback."""


class _InputChanged(RuntimeError):
    """An input changed while the analysis ran: pending again, the attempt is not counted."""


def _lock_path(config: dict, eid: str) -> Path:
    return PROJECT_ROOT / config["logs_dir"] / "locks" / f"{eid}.lock"


def _journal_path(config: dict, eid: str) -> Path:
    return PROJECT_ROOT / config["logs_dir"] / "publishing" / f"{eid}.json"


def _rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def output_dir_for(config: dict, entry: dict[str, Any]) -> Path:
    """The entry's output folder: the one kept in the manifest (chosen at its first analysis)."""
    return PROJECT_ROOT / entry["output_dir"]


def _refuse_foreign(final: Path, entry: dict[str, Any]) -> None:
    owner = mf.other_owner(final, entry)
    if owner:
        raise RuntimeError(f"output folder {final.relative_to(PROJECT_ROOT).as_posix()} holds "
                           f"the analysis of {owner}; nothing was changed there")


def _check_staged(staging: Path, summary: Any, eid: str) -> None:
    """Before anything is moved: the method's summary agrees with the files in staging."""
    try:
        outputs = summary["outputs"]
        for key in ("settings_sha256", "status", "checks"):
            summary[key]
        outputs["results.json"]
    except (KeyError, TypeError) as exc:
        raise AnalysisRefused(f"the method's summary lacks {exc}") from None
    try:
        json.loads((staging / "results.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisRefused(f"results.json is not valid JSON ({exc})") from None
    for name, digest in outputs.items():
        path = staging / name
        if Path(name).name != name or not path.is_file():
            raise AnalysisRefused(f"output {name} listed by the method is not in the staging folder")
        if sha256_file(path) != digest:
            raise AnalysisRefused(f"output {name} does not match the sha256 the method reported")
    try:
        record = json.loads((staging / "provenance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisRefused(f"provenance.json is missing or not valid JSON ({exc})") from None
    if record.get("entry_id") != eid:
        raise AnalysisRefused(f"provenance.json names {record.get('entry_id')}, not {eid}")
    if record.get("outputs") != outputs:
        raise AnalysisRefused("provenance.json lists other outputs than the method's summary")


def _changed_input(entry: dict[str, Any], input_path: Path, root: Path, spec: dict,
                   settings: dict, companions: dict | None) -> str | None:
    """The first input that is not what the run started with (its name), or None."""
    if sha256_file(input_path) != entry["sha256"]:
        return Path(entry["relative_path"]).name
    now = input_companions(spec, root, entry["relative_path"], settings)
    if now != companions:
        names = {k for k in (now or {}) if (now or {}).get(k) != (companions or {}).get(k)}
        names |= set(companions or {}) - set(now or {})
        return Path(sorted(names)[0]).name if names else "companions"
    return None


def analyze_entry(config: dict, eid: str, *, rerun: bool = False) -> dict[str, Any]:
    """Analyse one entry. rerun=True also redoes done/stale/failed entries (after steering).
    One process at a time per entry: the lock is held until the outputs are published."""
    with try_exclusive(_lock_path(config, eid)) as got:
        if not got:
            return {"entry_id": eid, "status": "skipped",
                    "reason": "being analysed by another run"}
        _recover_one(config, _journal_path(config, eid))
        return _analyze_locked(config, eid, rerun=rerun)


def _analyze_locked(config: dict, eid: str, *, rerun: bool) -> dict[str, Any]:
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
    companions = None   # {path: sha256} of the other files the method reads
    resolved = None     # the settings they were found with
    versions = versions_mod.current(entry["method"])   # of the code this run uses
    try:
        _refuse_foreign(final, entry)
        digest = sha256_file(input_path)
        if digest != entry["sha256"]:
            raise RuntimeError("file changed since the last scan; run `labpipe scan` first")
        resolved = settings_mod.resolve(entry["method"], entry["sample_id"],
                                        entry["measurement_id"])[0]
        companions = input_companions(spec, Path(root["path"]), entry["relative_path"], resolved)
        module = importlib.import_module(spec["module"])
        summary = module.run(input_path, staging, context={
            "sample_id": entry["sample_id"], "measurement_id": entry["measurement_id"],
            "relative_path": entry["relative_path"], "raw_root": root["id"],
            "method": entry["method"], "entry_id": eid, "output_dir": final.relative_to(PROJECT_ROOT).as_posix(),
        }, pipeline=True)
        _refuse_foreign(final, entry)
        _check_staged(staging, summary, eid)
        changed = _changed_input(entry, input_path, Path(root["path"]), spec, resolved, companions)
        if changed:
            raise _InputChanged(f"the input changed while it was analysed ({changed}); "
                                "analysed again on the next scan")
        status = "done"
    except Exception as exc:  # recorded, never retried endlessly
        reason = f"{type(exc).__name__}: {exc}"
        known = getattr(module, "AnalysisError", ()) if "module" in locals() else ()
        summary = None
        if isinstance(exc, _InputChanged):
            status, reason = "pending", str(exc)
        if not (isinstance(exc, (AnalysisRefused, _InputChanged)) or
                (known and isinstance(exc, known))):
            log = (PROJECT_ROOT / config["logs_dir"] / "errors"
                   / f"{eid}-{stamp}.txt")
            atomic_write_text(log, traceback.format_exc(), config)
            reason += f" (traceback: {log.relative_to(PROJECT_ROOT)})"

    journal = _journal_path(config, eid)
    if status == "done":
        # The journal says where everything is before the first rename; whoever finds it
        # later (recover_publications) knows a crash left the two renames half done.
        aside = _free(method_dir / "_superseded" / final.name / stamp)
        atomic_write_text(journal, json.dumps(
            {"schema": "labpipe.publishing/1", "entry_id": eid, "stamp": stamp,
             "staging": _rel(staging), "final": _rel(final), "aside": _rel(aside),
             "started_utc": utc_now()}, indent=1) + "\n", config)
        if final.exists() and any(final.iterdir()):
            _move(final, aside, config)
        _move(staging, final, config)
    elif staging.exists():
        _move(staging, _free(method_dir / "_failed" / final.name / stamp), config)

    if status == "pending":     # input changed during the run: nothing was published or counted
        with mf.locked(config) as data:
            stored = data["entries"][eid]
            mf.set_status(stored, "pending", reason)
            write_index(config, data, stored["sample_id"], stored["method"])
        return {"entry_id": eid, "status": "pending", "reason": reason, "output_dir": None,
                "review": None, "review_checks": None}

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
            if companions is not None:
                stored["analysed_companions"] = companions
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
    journal.unlink(missing_ok=True)     # published and recorded: nothing left to recover
    return {"entry_id": eid, "status": status, "reason": reason,
            "output_dir": final.relative_to(PROJECT_ROOT).as_posix() if status == "done" else None,
            "review": summary["status"] == "review" if summary else None,
            "review_checks": summary["checks"] if summary else None}


def _recover_one(config: dict, journal: Path) -> dict[str, Any] | None:
    """Roll back the publication a crash left half done (the entry's lock is held).

    Where things are tells what happened: the staging folder is gone only after the second
    rename, so then `final` holds the new outputs; otherwise `final` is the old outputs (crash
    before the first rename) or missing (between the renames; the old ones are in `aside`).
    The new outputs, or what is left in staging, go to _failed/<folder>/<stamp>-interrupted/,
    the old ones back, and the entry is pending. Nothing is deleted."""
    try:
        record = json.loads(journal.read_text(encoding="utf-8"))
        eid = record["entry_id"]
        staging, final, aside = (PROJECT_ROOT / record[k] for k in ("staging", "final", "aside"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError):
        return None         # not ours to guess at; left for a human
    kept = _free(final.parent / "_failed" / final.name / f"{record['stamp']}-interrupted")
    new_is_final = not staging.exists() and final.exists()
    if new_is_final:
        _move(final, kept, config)
    elif staging.exists():
        _move(staging, kept, config)
    restored = False
    if aside.exists() and not (final.exists() and any(final.iterdir())):
        if final.exists():
            final.rmdir()       # an empty folder
        _move(aside, final, config)
        restored = True
    reason = "publication was interrupted; analysed again"
    change = None
    with mf.locked(config) as data:
        stored = data["entries"].get(eid)
        if stored is not None:
            change = {"entry_id": eid, "relative_path": stored["relative_path"],
                      "from": stored.get("status"), "to": "pending", "reason": reason,
                      "kept_in": _rel(kept) if kept.exists() else None,
                      "earlier_outputs_back": restored}
            mf.set_status(stored, "pending", reason)
            write_index(config, data, stored.get("sample_id"), stored["method"])
    journal.unlink(missing_ok=True)
    return change or {"entry_id": eid, "to": "pending", "reason": reason}


def recover_publications(config: dict) -> list[dict[str, Any]]:
    """Roll back every interrupted publication (a journal in logs/publishing/) whose entry is
    not being analysed right now. Returns one change per recovered entry, for the scan."""
    folder = PROJECT_ROOT / config["logs_dir"] / "publishing"
    recovered = []
    for journal in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        with try_exclusive(_lock_path(config, journal.stem)) as got:
            if got and (change := _recover_one(config, journal)):
                recovered.append(change)
    return recovered


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
    Holds the entry's lock like an analysis does.
    """
    with try_exclusive(_lock_path(config, eid)) as got:
        if not got:
            return {"entry_id": eid, "status": "skipped",
                    "reason": "being analysed by another run"}
        _recover_one(config, _journal_path(config, eid))
        return _replot_locked(config, eid)


def _replot_locked(config: dict, eid: str) -> dict[str, Any]:
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
