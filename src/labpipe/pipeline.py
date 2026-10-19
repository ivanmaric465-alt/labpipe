"""Analyse manifest entries; maintain index.json files and the review queue.

Output layout: analysis/<sample_id>/<method>/<measurement_id>/{results.json, plot.png,
plot.pdf, provenance.json, notes.md}. Each run writes into a staging folder
next to the final one and is renamed into place only when complete. Earlier
outputs are moved to analysis/<sample_id>/<method>/_superseded/<measurement_id>/<stamp>/,
failed attempts to .../_failed/<measurement_id>/<stamp>/. Nothing is deleted.
"""

from __future__ import annotations

import importlib
import json
import os
import traceback
from pathlib import Path
from typing import Any

from labpipe import manifest as mf
from labpipe.provenance import code_version, sha256_file, utc_now
from labpipe.registry import load_methods
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, atomic_write_text

RUNNABLE = {"pending"}


def _stamp() -> str:
    return utc_now().replace(":", "").replace("-", "")


def _move(source: Path, target: Path, config: dict) -> None:
    assert_pipeline_writable(source, config)
    assert_pipeline_writable(target, config)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, target)


def output_dir_for(config: dict, entry: dict[str, Any]) -> Path:
    return (PROJECT_ROOT / config["analysis_dir"] / entry["sample_id"] / entry["method"]
            / entry["measurement_id"])


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
        entry = json.loads(json.dumps(entry))  # snapshot outside the lock
    spec = methods[entry["method"]]
    root = next(r for r in config["raw_roots"] if r["id"] == entry["raw_root"])
    input_path = Path(root["path"]) / entry["relative_path"]
    final = output_dir_for(config, entry)
    method_dir = final.parent
    stamp = _stamp()
    staging = method_dir / f".staging-{entry['measurement_id']}-{stamp}"
    assert_pipeline_writable(staging, config)

    status, reason, summary = "failed", None, None
    try:
        digest = sha256_file(input_path)
        if digest != entry["sha256"]:
            raise RuntimeError("file changed since the last scan; run `labpipe scan` first")
        module = importlib.import_module(spec["module"])
        summary = module.run(input_path, staging, context={
            "sample_id": entry["sample_id"], "measurement_id": entry["measurement_id"],
            "relative_path": entry["relative_path"], "raw_root": root["id"],
            "entry_id": eid, "output_dir": str(final.relative_to(PROJECT_ROOT)),
        }, pipeline=True)
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
            _move(final, method_dir / "_superseded" / entry["measurement_id"] / stamp, config)
        _move(staging, final, config)
    elif staging.exists():
        _move(staging, method_dir / "_failed" / entry["measurement_id"] / stamp, config)

    with mf.locked(config) as data:
        stored = data["entries"][eid]
        stored["attempts"] = stored.get("attempts", 0) + 1
        stored["code_version"] = code_version()
        stored["last_processed_utc"] = utc_now()
        if status == "done":
            stored["output_dir"] = str(final.relative_to(PROJECT_ROOT))
            stored["results_sha256"] = summary["outputs"]["results.json"]
            stored["settings_sha256"] = summary["settings_sha256"]
            stored["review"] = summary["status"] == "review"
            stored["review_codes"] = sorted({c["code"] for c in summary["checks"]})
            mf.set_status(stored, "done", "analysis complete" + (
                " (flagged for human review)" if stored["review"] else ""))
        else:
            mf.set_status(stored, "failed", reason)
        write_index(config, data, stored["sample_id"], stored["method"])
    return {"entry_id": eid, "status": status, "reason": reason,
            "output_dir": str(final.relative_to(PROJECT_ROOT)) if status == "done" else None,
            "review": summary["status"] == "review" if summary else None,
            "review_checks": summary["checks"] if summary else None}


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
                                               "done_flagged": [], "stale": []}
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
    lines = [f"# Review queue ({utc_now()})", ""]
    unrecognised = sorted(data.get("unrecognised", {}).values(),
                          key=lambda r: (r["raw_root"], r["relative_path"]))
    lines.append(f"## Not recognised by any method ({len(unrecognised)})")
    lines.append("Tell the agent what these are (it can add them to a method or create one), "
                 "or list them in config/ignore.yaml to leave them alone.")
    for record in unrecognised:
        lines.append(f"- `{record['relative_path']}` ({record['raw_root']}, first seen "
                     f"{record['first_seen_utc']})")
    lines.append("")
    titles = {
        "needs_review": "Needs a human decision (answer in config/sample_ids.yaml)",
        "failed": "Failed analyses",
        "done_flagged": "Analysed, flagged for review (see notes.md / results.json checks)",
        "stale": "Analysed with an older code version (`labpipe requeue --stale` to redo)",
    }
    for key, title in titles.items():
        lines.append(f"## {title} ({len(groups[key])})")
        for entry in sorted(groups[key], key=lambda e: e["relative_path"]):
            detail = entry.get("status_reason") or ""
            if key == "done_flagged":
                detail = ", ".join(entry.get("review_codes") or [])
            location = entry.get("output_dir") or ""
            lines.append(f"- `{entry['relative_path']}` ({entry['entry_id']}) {location} — {detail}")
        lines.append("")
    atomic_write_text(PROJECT_ROOT / config["logs_dir"] / "review_queue.md", "\n".join(lines),
                      config)
    return {"not_recognised": len(unrecognised)} | {key: len(value) for key, value in groups.items()}
