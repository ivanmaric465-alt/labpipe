"""`labpipe dataset`: every sample, its properties and the named quantities of its measurements.

Each method declares what is meaningful to compare in `quantities(results)` of its summary
module (methods.yaml → `summary`); a method without one has no quantities. Values are copied
from results.json (and config/samples.yaml for sample properties), never computed here. Image
methods (`report: gallery`, e.g. SEM) are summed up per sample under `galleries`.

Samples come in the order of the report's `sample_order` setting (config/report.yaml; natural
order by name by default).

`compare` does the arithmetic across samples, and nothing else: the difference from a
reference sample, or an unweighted straight-line fit against another quantity. It never picks
among several measurements silently, and it lists what it left out and why.
"""

from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
from typing import Any

import yaml

from labpipe import manifest as mf
from labpipe import migrations
from labpipe.provenance import utc_now
from labpipe.registry import load_methods
from labpipe.report import report_settings, sample_sorter
from labpipe.safety import PROJECT_ROOT, atomic_write_text

SCHEMA = "labpipe.dataset/1"
COMPARE_SCHEMA = "labpipe.compare/1"
SKIPPED = ("ignored", "duplicate")           # not measurements of their own
WITH_RESULTS = ("done", "stale", "missing")  # statuses whose results.json is current enough to read
TREND_METHOD = "ordinary least squares, unweighted; point σ not used as weights"
SAMPLES_FILE = "config/samples.yaml"


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sample_properties(sample_id: str, samples: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Numeric scalars of config/samples.yaml → samples.<id>, as quantities.

    - `eds.nominal_at_percent.<El>` (also through the sample's presets) → `sample.nominal.<El>`;
    - numbers in `samples.<id>.properties` (e.g. `loading_wt: 20`) → `sample.<name>`.
    Analysis settings are not sample properties and are left out.
    """
    entry = (samples.get("samples") or {}).get(sample_id) or {}
    out: dict[str, dict[str, Any]] = {}
    base = f"{SAMPLES_FILE} → samples.{sample_id}"
    nominal, where = None, None
    for preset in entry.get("presets") or []:
        layer = ((samples.get("presets") or {}).get(preset) or {}).get("eds") or {}
        if isinstance(layer.get("nominal_at_percent"), dict):
            nominal, where = layer["nominal_at_percent"], f"{SAMPLES_FILE} → presets.{preset}"
    own = (entry.get("eds") or {}).get("nominal_at_percent")
    if isinstance(own, dict):
        nominal, where = own, base
    for element, value in (nominal or {}).items():
        if _number(value):
            out[f"sample.nominal.{element}"] = {
                "value": float(value), "sigma": None, "unit": "at%",
                "source": f"{where}.eds.nominal_at_percent.{element}"}
    for name, value in (entry.get("properties") or {}).items():
        if _number(value):
            out[f"sample.{name}"] = {"value": float(value), "sigma": None, "unit": "",
                                     "source": f"{base}.properties.{name}"}
    return out


def _quantities(spec: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    if not spec.get("summary"):
        return {}
    module = importlib.import_module(spec["summary"])
    return module.quantities(results) if hasattr(module, "quantities") else {}


def _measurement(entry: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    out_dir = entry.get("output_dir")
    folder = PROJECT_ROOT / out_dir if out_dir else None

    def exists(name: str) -> str | None:
        return f"{out_dir}/{name}" if folder and (folder / name).exists() else None

    row = {"entry_id": entry["entry_id"], "method": entry["method"],
           "measurement_id": entry["measurement_id"], "status": entry["status"],
           "output_dir": out_dir, "results_sha256": entry.get("results_sha256"),
           "notes": exists("notes.md"), "plot": exists("plot.png"),
           "review_codes": list(entry.get("review_codes") or []), "quantities": {}}
    if entry["status"] in WITH_RESULTS and exists("results.json"):
        results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        try:
            row["quantities"] = _quantities(spec, results)
        except (KeyError, TypeError, IndexError) as exc:  # e.g. results of an older layout
            row["quantities_error"] = f"{type(exc).__name__}: {exc}"
    return row


def build(config: dict) -> dict[str, Any]:
    methods = load_methods()
    data = mf.load(config)
    samples_path = PROJECT_ROOT / SAMPLES_FILE
    samples_cfg = (migrations.upgraded(
        samples_path, yaml.safe_load(samples_path.read_text(encoding="utf-8")) or {},
        PROJECT_ROOT) if samples_path.exists() else {})
    rows: dict[str, list[dict[str, Any]]] = {sample: [] for sample in samples_cfg.get("samples") or {}}
    galleries: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in data["entries"].values():
        if entry.get("method") not in methods or entry.get("status") in SKIPPED:
            continue
        sample = entry.get("sample_id") or "?"
        rows.setdefault(sample, [])
        if methods[entry["method"]].get("report") == "gallery":
            # image methods (SEM): one summary per sample, not one row per image
            item = _measurement(entry, methods[entry["method"]])
            block = galleries.setdefault(sample, {}).setdefault(
                entry["method"], {"images": 0, "statuses": {}, "plots": []})
            block["images"] += 1
            block["statuses"][item["status"]] = block["statuses"].get(item["status"], 0) + 1
            if item["plot"]:
                block["plots"].append(item["plot"])
            continue
        rows[sample].append(_measurement(entry, methods[entry["method"]]))
    out = {}
    order = sample_sorter(report_settings(methods)["report"]["sample_order"], samples_cfg)
    for sample in sorted(rows, key=order):   # the report's `sample_order` setting
        out[sample] = {
            "properties": sample_properties(sample, samples_cfg),
            "measurements": sorted(rows[sample], key=lambda m: (m["method"], m["measurement_id"],
                                                                m["entry_id"]))}
        if sample in galleries:
            out[sample]["galleries"] = {method: {**block, "plots": sorted(block["plots"])}
                                        for method, block in sorted(galleries[sample].items())}
    return {"schema": SCHEMA, "generated_utc": utc_now(), "samples": out}


# --- compare ---------------------------------------------------------------------------------

def _candidates(block: dict[str, Any], quantity: str) -> list[dict[str, Any]]:
    """The values of `quantity` in one sample: its property, or one per measurement carrying it."""
    if quantity in block["properties"]:
        prop = block["properties"][quantity]
        return [{"entry_id": None, "measurement_id": None, "value": prop["value"],
                 "sigma": prop.get("sigma"), "unit": prop["unit"], "review_codes": [],
                 "stale": False, "status": None}]
    return [{"entry_id": m["entry_id"], "measurement_id": m["measurement_id"],
             "value": m["quantities"][quantity]["value"],
             "sigma": m["quantities"][quantity].get("sigma"),
             "unit": m["quantities"][quantity]["unit"], "review_codes": m["review_codes"],
             "stale": m["status"] == "stale", "status": m["status"]}
            for m in block["measurements"] if quantity in m["quantities"]]


def _why_missing(block: dict[str, Any], quantity: str) -> str:
    if quantity.startswith("sample."):
        return f"no such property ({quantity})"
    method = quantity.split(".")[0]
    mine = [m for m in block["measurements"] if m["method"] == method]
    if not mine:
        return f"no {method} measurement"
    if any(m["status"] in WITH_RESULTS for m in mine):
        return f"no such quantity in its {method} results ({quantity})"
    statuses = sorted({m["status"] for m in mine})
    return ("failed" if statuses == ["failed"] else
            f"not analysed (status: {', '.join(statuses)})")


def _pick(found: list[dict[str, Any]], measurements: list[str] | None,
          each: bool) -> tuple[list[dict[str, Any]], str | None]:
    """The values to use from one sample, or why none (several without a choice)."""
    if len(found) <= 1:
        return found, None
    chosen = [c for c in found if {c["entry_id"], c["measurement_id"]} & set(measurements or [])]
    if chosen:
        return chosen, None
    if each:
        return found, None
    return [], "several measurements: choose --measurement or --each"


def _point(sample: str, y: dict[str, Any]) -> dict[str, Any]:
    return {"sample": sample, "entry_id": y["entry_id"], "measurement_id": y["measurement_id"],
            "y": y["value"], "y_sigma": y["sigma"], "review_codes": y["review_codes"],
            "stale": y["stale"], "status": y["status"]}


def _check_measurements(dataset: dict[str, Any], measurements: list[str] | None) -> None:
    known = {m[key] for block in dataset["samples"].values() for m in block["measurements"]
             for key in ("entry_id", "measurement_id")}
    unknown = sorted(set(measurements or []) - known)
    if unknown:
        raise ValueError(f"no such measurement: {', '.join(unknown)}")


def _line(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Ordinary least squares y = intercept + slope·x over the points (standard errors need n ≥ 3)."""
    n = len(points)
    result: dict[str, Any] = {"n": n, "slope": None, "slope_sigma": None, "intercept": None,
                              "intercept_sigma": None, "r_squared": None, "reason": None}
    if n < 2:
        result["reason"] = "fewer than 2 points"
        return result
    xs, ys = [p["x"] for p in points], [p["y"] for p in points]
    x_mean, y_mean = sum(xs) / n, sum(ys) / n
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx == 0:
        result["reason"] = "all x values are equal"
        return result
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sxx
    intercept = y_mean - slope * x_mean
    ssr = sum((y - intercept - slope * x) ** 2 for x, y in zip(xs, ys))
    sst = sum((y - y_mean) ** 2 for y in ys)
    result |= {"slope": slope, "intercept": intercept,
               "r_squared": (1 - ssr / sst) if sst else None}
    if n >= 3:
        s2 = ssr / (n - 2)
        result["slope_sigma"] = math.sqrt(s2 / sxx)
        result["intercept_sigma"] = math.sqrt(s2 * (1 / n + x_mean ** 2 / sxx))
    else:
        result["reason"] = ("two points: the line passes through both, so R² is 1 by construction; "
                            "standard errors need at least 3 points")
    return result


def compare(config: dict, quantity: str, *, reference: str | None = None,
            against: str | None = None, measurements: list[str] | None = None,
            each: bool = False, dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Difference from a reference sample (`reference`) or a linear trend against another
    quantity (`against`). Several measurements of a sample: `measurements` picks, `each` takes
    all; otherwise the sample is left out and says so. Stale values are used and flagged."""
    if (reference is None) == (against is None):
        raise ValueError("give exactly one of reference or against")
    dataset = dataset or build(config)
    _check_measurements(dataset, measurements)
    blocks = dataset["samples"]
    if reference is not None and reference not in blocks:
        raise ValueError(f"no such sample: {reference}")
    operation = "difference" if reference is not None else "trend"
    payload: dict[str, Any] = {"schema": COMPARE_SCHEMA, "operation": operation,
                               "quantity": quantity, "method": None, "points": [],
                               "left_out": [], "result": None, "units": {}}
    payload["reference" if reference is not None else "against"] = reference or against
    left_out, points = payload["left_out"], payload["points"]
    ref_value = None
    if reference is not None:
        found = _candidates(blocks[reference], quantity)
        chosen, problem = _pick(found, measurements, False)
        if len(chosen) != 1:
            raise ValueError(f"reference sample {reference}: "
                             + (problem or _why_missing(blocks[reference], quantity)))
        ref_value = chosen[0]
        payload["reference_point"] = {"sample": reference, **{k: ref_value[k] for k in (
            "entry_id", "measurement_id", "value", "sigma", "review_codes", "stale")}}

    for sample, block in blocks.items():
        if sample == reference:
            continue
        ys, problem = _pick(_candidates(block, quantity), measurements, each)
        if not ys:
            left_out.append({"sample": sample, "reason": problem or _why_missing(block, quantity)})
            continue
        if operation == "difference":
            for y in ys:
                point = _point(sample, y)
                both = y["sigma"] is not None and ref_value["sigma"] is not None
                point["difference"] = y["value"] - ref_value["value"]
                point["difference_sigma"] = (math.hypot(y["sigma"], ref_value["sigma"])
                                             if both else None)
                points.append(point)
            payload["units"]["quantity"] = ys[0]["unit"]
            continue
        xs, problem = _pick(_candidates(block, against), measurements, each)
        if not xs:
            left_out.append({"sample": sample, "reason": f"{against}: "
                             + (problem or _why_missing(block, against))})
            continue
        for y in ys:  # with --each, every y measurement is paired with every x measurement
            for x in xs:
                point = _point(sample, y)
                point |= {"x": x["value"], "x_sigma": x["sigma"], "x_entry_id": x["entry_id"],
                          "x_review_codes": x["review_codes"],
                          "stale": y["stale"] or x["stale"]}
                points.append(point)
        payload["units"] |= {"quantity": ys[0]["unit"], "against": xs[0]["unit"]}

    if operation == "difference":
        payload["method"] = "sample value minus reference value; σ = √(σ₁² + σ₂²) when both are given"
        payload["result"] = {"n": len(points), "reference_value": ref_value["value"],
                             "reference_sigma": ref_value["sigma"]}
    else:
        payload["method"] = TREND_METHOD
        payload["result"] = _line(points)
        if "quantity" in payload["units"] and "against" in payload["units"]:
            payload["units"]["slope"] = f"{payload['units']['quantity']} per {payload['units']['against']}"
    return payload


def save(run_dir: Path, payload: dict[str, Any], config: dict | None = None) -> Path:
    """Write payload as the next free compare-N.json (N from 1) in run_dir, atomically."""
    run_dir = Path(run_dir)
    number = 1
    while (run_dir / f"compare-{number}.json").exists():
        number += 1
    target = run_dir / f"compare-{number}.json"
    atomic_write_text(target, json.dumps(payload, indent=1, ensure_ascii=False) + "\n", config)
    return target
