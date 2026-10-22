"""XRD comparison figures (the `compare:` hook of methods.yaml; see docs/methods/xrd.md).

- `patterns` (kind `lines`): the measured patterns stacked on one 2θ axis. Each trace is read
  from the raw file (read-only, with the method's own readers) after its sha256 was checked
  against `results["input"]["sha256"]`, thinned to at most `MAX_POINTS` points (per bucket the
  lowest and the highest point are kept, in 2θ order, so every peak survives); a measurement
  whose file is missing or changed is left out and the caption says why. The tick rows are the
  predicted lines of the reference phases the analyses found to be candidates
  (`phases.reference_matches`, copied as recorded).
- `lattice-<phase>` (kind `dots`): the lattice parameter a of the fcc phase per measurement
  (the values of `xrd.a.<phase>` from `summary.quantities`), with the a of the candidate
  references as vertical lines (`reference_comparison`, copied as recorded).

Nothing is computed here except the display thinning; every number is copied from results.json
or from the raw file. This module is part of the compare version, not of the analysis version.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from labpipe.comparekinds import lines
from labpipe.methods.xrd.io_xrdml import DEFAULT_MAX_BYTES, read_xrdml
from labpipe.methods.xrd.io_xy import read_xy
from labpipe.methods.xrd.summary import quantities
from labpipe.provenance import sha256_file

COVERS = ["xrd.a.*"]          # the lattice figures replace the generic dot plots of a
MAX_POINTS = 2000             # points per trace after thinning (2 per bucket)
THINNING = (f"at most {MAX_POINTS} points: the points are cut into {MAX_POINTS // 2} buckets of "
            "consecutive points and each bucket keeps its lowest and its highest intensity, "
            "in 2θ order")


def _thin(x: np.ndarray, y: np.ndarray) -> tuple[list[float], list[float]]:
    n = x.size
    if n <= MAX_POINTS:
        return x.tolist(), y.tolist()
    buckets = MAX_POINTS // 2
    keep: list[int] = []
    for b in range(buckets):
        start, stop = b * n // buckets, (b + 1) * n // buckets
        chunk = y[start:stop]
        keep += [start + int(np.argmin(chunk)), start + int(np.argmax(chunk))]
    index = np.unique(np.array(keep))
    return x[index].tolist(), y[index].tolist()


def _trace(m: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """(trace, "") or (None, why the measurement is left out)."""
    recorded = (m.get("input") or {}).get("sha256")
    path = m.get("raw_path")
    if not path:
        return None, "the raw file is not found here"
    if not recorded:
        return None, "results.json records no sha256 of the raw file"
    if sha256_file(Path(path)) != recorded:
        return None, "the raw file differs from the one analysed (sha256)"
    settings = config.get("input") or {}
    limit = settings.get("max_bytes", DEFAULT_MAX_BYTES)
    try:
        reader = read_xrdml if Path(path).suffix.lower() == ".xrdml" else read_xy
        pattern = reader(path, max_bytes=limit)
        scan = pattern.scans[int(settings.get("scan_index", 0))]
    except (ValueError, OSError, UnicodeError, IndexError) as exc:
        return None, f"the raw file cannot be read ({exc})"
    x = np.asarray(scan.two_theta, dtype=float)
    y = np.asarray(scan.intensity, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[ok], kind="stable")
    xs, ys = _thin(x[ok][order], y[ok][order])
    if len(xs) < 2:
        return None, "the raw file has fewer than 2 points"
    return ({"sample": m["sample"], "measurement_id": m["measurement_id"],
             "entry_id": m["entry_id"], "x": xs, "y": ys, "n_points_in_file": int(ok.sum()),
             "thinning": THINNING if int(ok.sum()) > MAX_POINTS else "none (all points)",
             "source": {"path": path, "sha256": recorded,
                        "relative_path": (m.get("input") or {}).get("relative_path")}}, "")


def _candidates(m: dict[str, Any]) -> list[dict[str, Any]]:
    matches = ((m["results"].get("phases") or {}).get("reference_matches")) or []
    return [r for r in matches if r.get("is_candidate")]


def _patterns(measurements: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    config = context.get("config") or {}
    traces, skipped, rows = [], [], {}
    for m in measurements:
        trace, why = _trace(m, config)
        if trace is None:
            skipped.append({"sample": m["sample"], "measurement_id": m["measurement_id"],
                            "reason": why})
            continue
        traces.append(trace)
        for ref in _candidates(m):
            found = [line["two_theta_deg"] for key in ("matched", "unmatched")
                     for line in ref.get(key) or []]
            rows.setdefault(ref["phase"], found)       # the first measurement's predicted lines
    if not traces:
        return None
    spec = {"kind": "lines", "name": "patterns", "title": "XRD patterns",
            "x_label": "2θ (°)", "y_label": "Intensity (counts)", "layout": "stacked",
            "y_scale": (config.get("plot") or {}).get("y_scale", "linear"),
            "traces": traces, "skipped": skipped,
            "tick_rows": [{"label": f"{label} (reference)",
                           "positions": sorted(rows[label])} for label in sorted(rows)]}
    text = lines.caption(spec) + (
        f" Traces are thinned to at most {MAX_POINTS} points (lowest and highest point of each "
        "bucket kept, so every peak survives).")
    if skipped:
        text += " Left out: " + "; ".join(
            f"{s['measurement_id']} ({s['reason']})" for s in skipped) + "."
    spec["caption"] = text
    return spec


def _lattice(measurements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    samples: list[str] = []
    phases: dict[str, dict[str, Any]] = {}
    for m in measurements:
        if m["sample"] not in samples:
            samples.append(m["sample"])
        try:
            found = quantities(m["results"])
        except (KeyError, TypeError, IndexError):
            continue                                    # a results.json this code cannot read
        candidates = {r["phase"] for r in _candidates(m)}
        for name, q in found.items():
            if not name.startswith("xrd.a."):
                continue
            phase = name[len("xrd.a."):]
            entry = phases.setdefault(phase, {"points": [], "refs": {}})
            entry["points"].append({
                "sample": m["sample"], "entry_id": m["entry_id"],
                "measurement_id": m["measurement_id"], "value": q["value"],
                "sigma": q.get("sigma"), "nominal": None, "unit": q["unit"],
                "status": m["status"], "results_path": m["results_path"],
                "results_sha256": m["results_sha256"], "key": q["key"]})
            index = re.search(r"fcc_indexed\[(\d+)\]", q["key"])
            if index:
                rows = m["results"]["phases"]["fcc_indexed"][int(index.group(1))]
                for row in rows.get("reference_comparison") or []:
                    if row["phase"] in candidates:
                        entry["refs"][(row["phase"], row["a_reference_angstrom"])] = row
    figures = []
    for phase, entry in phases.items():
        spec = {"kind": "dots", "name": f"lattice-{phase}",
                "title": f"Lattice parameter a, {phase}", "unit": "Å",
                "label": f"Lattice parameter a, {phase} (Å)",
                "samples": [s for s in samples if any(p["sample"] == s for p in entry["points"])],
                "points": entry["points"],
                "reference_lines": [{"value": value, "label": label}
                                    for label, value in sorted(entry["refs"])]}
        if spec["reference_lines"]:
            spec["caption"] = (
                f"Lattice parameter a of the {phase} phase per measurement, σ from the fit. "
                "Dashed lines: a of the reference phases found to be candidates in at least one "
                "measurement (" + ", ".join(r["label"] for r in spec["reference_lines"])
                + "); closeness to a line does not identify a phase.")
        figures.append(spec)
    return figures


def figures(measurements: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    patterns = _patterns(measurements, context)
    return ([patterns] if patterns else []) + _lattice(measurements)
