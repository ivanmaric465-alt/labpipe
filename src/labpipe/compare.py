"""`labpipe compare`: one figure per named quantity, the samples side by side (code only).

For each method's quantity (`quantities(results)` of its summary module, see `labpipe dataset`)
that at least two samples have, a dot plot: the samples down the vertical axis in the report's
sample order, the value along the horizontal axis with its σ as an error bar, one point per
measurement (nothing averaged; several measurements of a sample sit side by side in its row).
A value without σ is a hollow marker. Every sample keeps its colour and marker in every figure.
Values are copied from results.json, never computed here.

    analysis/_compare/<method>/<quantity>/plot.png, plot.pdf, provenance.json

`provenance.json` holds the plotted data itself (per point: sample, measurement, value, σ, unit,
the results.json it came from with its sha256 and the key in it), the sample order and styles,
the resolved plot style, and the code (commit and `compare_version`, a hash of this module and
the plotting modules; no analysis or plot version of a method includes it). `labpipe replay`
redraws the figure from that record alone. A figure is redrawn only when its recorded inputs
(data, order, style, compare_version) differ from the current ones; the old one is moved to
analysis/_compare/_superseded/<stamp>/ (nothing is deleted), and so is the folder of a quantity
that no longer exists. matplotlib is imported only when something is drawn.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from labpipe import dataset
from labpipe import settings as settings_mod
from labpipe import versions
from labpipe.provenance import (canonical_json, environment, git_info, runtime, sha256_bytes,
                                sha256_file, utc_now)
from labpipe.registry import load_methods
from labpipe.report import compare_wanted, report_settings
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, atomic_write_text

SCHEMA = "labpipe.compare-figure/1"
ENTRY_POINT = "labpipe.compare.render"
FOLDER = "_compare"
MIN_SAMPLES = 2

# The same values as config/plots.yaml → compare (a test keeps them equal); used for a
# provenance record that has none.
DEFAULT_STYLE = {
    "width_in": "single", "height_per_sample_in": 0.3, "min_height_in": 1.6,
    "palette": "okabe-ito", "marker_cycle": ["o", "s", "^", "D", "v", "P"], "marker_size": 4.5,
    "point_offset": 0.2,
}
# Fixed margins of the figure (inches); `keep_inside` widens the left one for long names.
MARGIN_IN = {"top": 0.12, "bottom": 0.5, "bottom_two_lines": 0.72, "left": 0.55, "right": 0.12}


def _stamp() -> str:
    return utc_now().replace(":", "").replace("-", "")


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def folder_name(quantity: str) -> str:
    """The folder of a quantity: its name, with characters unsafe in a path replaced."""
    return re.sub(r"[^A-Za-z0-9_.+-]+", "-", quantity)


def base_dir(config: dict) -> Path:
    return PROJECT_ROOT / config["analysis_dir"] / FOLDER


def _move(source: Path, target: Path, config: dict) -> None:
    assert_pipeline_writable(source, config)
    assert_pipeline_writable(target, config)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, target)


def _aside(base: Path, stamp: str, method: str, name: str) -> Path:
    """Where a figure folder goes when it is replaced or no longer exists (never deleted); a
    second move in the same second gets a number."""
    target = base / "_superseded" / stamp / method / name
    n = 1
    while target.exists():
        n += 1
        target = base / "_superseded" / f"{stamp}-{n}" / method / name
    return target


# --- the data ----------------------------------------------------------------------------------

def _sort_key(order: dict[str, int]):
    """Quantities of a method: groups in the order they first appear; inside a group (the
    elements of eds.at.<El>) by atomic number, then as they appear."""
    from labpipe.methods.eds.summary import ATOMIC_NUMBER
    first: dict[str, int] = {}
    for quantity, place in order.items():
        group = quantity.rpartition(".")[0]
        first[group] = min(first.get(group, place), place)

    def key(quantity: str) -> tuple:
        group, _, last = quantity.rpartition(".")
        return (first[group], ATOMIC_NUMBER.get(last, 10_000), order[quantity])
    return key


def offered(ds: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """{(method, quantity): points}: every measurement's value of every named quantity, in the
    sample order of the dataset, then by measurement. Values that are not finite are left out."""
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    for sample, block in ds["samples"].items():
        for item in block["measurements"]:
            path = f"{item['output_dir']}/results.json" if item["output_dir"] else None
            for quantity, value in (item.get("quantities") or {}).items():
                if not _number(value.get("value")) or path is None:
                    continue
                if path not in hashes:
                    hashes[path] = sha256_file(PROJECT_ROOT / path)
                sigma = value.get("sigma")
                found.setdefault((item["method"], quantity), []).append({
                    "sample": sample, "entry_id": item["entry_id"],
                    "measurement_id": item["measurement_id"], "value": value["value"],
                    "sigma": sigma if _number(sigma) else None, "unit": value.get("unit") or "",
                    "status": item["status"], "results_path": path,
                    "results_sha256": hashes[path], "key": value.get("key")})
    return found


def _sample_styles(ds: dict[str, Any], style: dict[str, Any]) -> dict[str, dict[str, str]]:
    """One colour and marker per sample of the dataset, by its place in the sample order, so a
    sample looks the same in every figure."""
    from labpipe.plotting.acs import palette
    colours = palette({"palette": style["palette"]})
    markers = style["marker_cycle"]
    return {sample: {"color": colours[i % len(colours)], "marker": markers[i % len(markers)]}
            for i, sample in enumerate(ds["samples"])}


def figure_spec(method: str, quantity: str, points: list[dict[str, Any]],
                styles: dict[str, dict[str, str]], plot: dict[str, Any]) -> dict[str, Any]:
    """Everything the figure is drawn from (what provenance.json records): no other input."""
    samples = list(dict.fromkeys(p["sample"] for p in points))
    return {"method": method, "quantity": quantity, "unit": points[0]["unit"], "points": points,
            "samples": [{"sample": s, **styles[s]} for s in samples], "plot": plot}


def inputs_sha256(spec: dict[str, Any], compare_version: str) -> str:
    """What decides whether a figure is current: data, order, styles, plot style, code version."""
    return sha256_bytes(canonical_json({
        "method": spec["method"], "quantity": spec["quantity"], "unit": spec["unit"],
        "points": spec["points"], "samples": spec["samples"], "plot": spec["plot"],
        "compare_version": compare_version}))


def caption(spec: dict[str, Any]) -> str:
    """What the figure shows and what its bars are; the report's caption and alt text."""
    points, samples = spec["points"], spec["samples"]
    unit = f" ({spec['unit']})" if spec["unit"] else ""
    counts = {s["sample"]: sum(1 for p in points if p["sample"] == s["sample"]) for s in samples}
    several = [f"{name} ×{n}" for name, n in counts.items() if n > 1]
    text = (f"{spec['quantity']}{unit}: {len(points)} measurement{'s' if len(points) != 1 else ''}"
            f" of {len(samples)} samples, one point each, nothing averaged"
            + (f" ({', '.join(several)})" if several else "") + ". ")
    bare = [p["measurement_id"] for p in points if p["sigma"] is None]
    if not bare:
        text += "Error bars: σ from the fit."
    elif len(bare) == len(points):
        text += "No σ for any point (hollow markers), so no error bars."
    else:
        shown = ", ".join(bare[:5]) + (f" and {len(bare) - 5} more" if len(bare) > 5 else "")
        text += f"Error bars: σ from the fit; hollow markers have no σ ({shown})."
    stale = sum(1 for p in points if p.get("status") == "stale")
    if stale:
        text += f" {stale} point{'s are' if stale != 1 else ' is'} from an analysis that is stale."
    return text


# --- drawing -----------------------------------------------------------------------------------

def _axis_label(quantity: str, unit: str) -> str:
    name, unit = quantity.replace("$", r"\$"), unit.replace("$", r"\$")
    if not unit:
        return name
    return f"{name} ({unit})" if len(name) + len(unit) <= 30 else f"{name}\n({unit})"


def render(spec: dict[str, Any], stem: Path) -> dict[str, Any]:
    """Draw the dot plot of `spec` as stem.png (+ the other formats of the plot style).
    Deterministic: the same spec gives the same bytes."""
    from labpipe.plotting.acs import apply_acs, keep_inside, save_figure, style_of, width_in
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    plot = style_of({"plots": spec["plot"]})
    style = DEFAULT_STYLE | dict(spec["plot"].get("compare") or {})
    font = apply_acs(plot)
    rows = spec["samples"]
    label = _axis_label(spec["quantity"], spec["unit"])
    height = max(style["min_height_in"], len(rows) * style["height_per_sample_in"]
                 + MARGIN_IN["top"] + (MARGIN_IN["bottom_two_lines"] if "\n" in label
                                       else MARGIN_IN["bottom"]))
    width = width_in({"width_in": style["width_in"]}, plot)
    figure, axis = plt.subplots(figsize=(width, height))
    bottom = MARGIN_IN["bottom_two_lines"] if "\n" in label else MARGIN_IN["bottom"]
    figure.subplots_adjust(left=MARGIN_IN["left"] / width, right=1 - MARGIN_IN["right"] / width,
                           bottom=bottom / height, top=1 - MARGIN_IN["top"] / height)
    for index, row in enumerate(rows):
        axis.axhline(index, color="0.9", lw=0.5, zorder=0)
        mine = [p for p in spec["points"] if p["sample"] == row["sample"]]
        step = min(style["point_offset"], 0.7 / (len(mine) - 1)) if len(mine) > 1 else 0.0
        for n, point in enumerate(mine):
            y = index + (n - (len(mine) - 1) / 2) * step
            hollow = point["sigma"] is None
            axis.errorbar(point["value"], y, xerr=None if hollow else point["sigma"],
                          fmt=row["marker"], color=row["color"], ms=style["marker_size"],
                          mfc="white" if hollow else row["color"], mec=row["color"], mew=0.9,
                          capsize=1.8, elinewidth=0.8, capthick=0.8, zorder=3)
    axis.set_ylim(len(rows) - 0.5, -0.5)        # the first sample on top
    axis.set_yticks(range(len(rows)))
    axis.set_yticklabels([r["sample"].replace("$", r"\$") for r in rows])
    axis.tick_params(axis="y", length=0)
    axis.margins(x=0.08)
    axis.xaxis.set_major_locator(MaxNLocator(nbins=5))
    axis.ticklabel_format(axis="x", style="plain", useOffset=False)
    axis.set_xlabel(label)
    keep_inside(figure)
    files = save_figure(figure, stem, plot, int(plot["dpi"]))
    plt.close(figure)
    return {"font": font, "files": files}


# --- build ---------------------------------------------------------------------------------------

def _record(spec: dict[str, Any], folder: Path, files: list[str], font: dict[str, str],
            digest: str, version: dict[str, Any], where: str, sources: list) -> dict[str, Any]:
    """provenance.json of a figure drawn into `folder` (it will be at `where`)."""
    outputs = {name: sha256_file(folder / name) for name in sorted(files)}
    return {
        "schema": SCHEMA, "created_utc": utc_now(), "method": spec["method"],
        "quantity": spec["quantity"], "unit": spec["unit"], "caption": caption(spec),
        "inputs_sha256": digest, "points": spec["points"], "samples": spec["samples"],
        "plot": spec["plot"], "plot_sources": sources,
        "code": git_info() | version | {"labpipe_entry": ENTRY_POINT},
        "font": font, "environment": environment(), "runtime": runtime(), "outputs": outputs,
        "command": " ".join(shlex.quote(arg) for arg in sys.argv),
        "output_dir": where, "replay": f"labpipe replay {where}/provenance.json"}


def _intact(folder: Path, old: dict[str, Any]) -> bool:
    """The recorded figure files are there and unchanged."""
    return bool(old.get("outputs")) and all(
        (folder / name).exists() and sha256_file(folder / name) == digest
        for name, digest in old["outputs"].items())


def _read(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("schema") == SCHEMA else None


def _wanted_by(config_all: dict[str, dict], method: str, quantity: str) -> bool:
    return any(compare_wanted(s["compare"], method, quantity) for s in config_all.values())


def build(config: dict, *, method: str | None = None) -> dict[str, Any]:
    """(Re)build the comparison figures. Which quantities: what the reports with a `compare`
    section ask for (all reports when none has it). `method`: only that method's figures.
    Returns the figures (drawn, redrawn or kept), what was skipped and why, the settings'
    names no measurement offers, and the folders moved aside."""
    methods = load_methods()
    if method is not None and method not in methods:
        raise ValueError(f"unknown method {method!r}; known: {', '.join(sorted(methods))}")
    reports = report_settings(methods)
    using = {k: v for k, v in reports.items() if "compare" in v["sections"]} or reports
    ds = dataset.build(config)
    found = offered(ds)
    plot, sources = settings_mod.plot_style()
    styles = _sample_styles(ds, DEFAULT_STYLE | plot["compare"])
    version = versions.compare_version()
    stamp = _stamp()
    base = base_dir(config)
    out: dict[str, Any] = {"dir": base.relative_to(PROJECT_ROOT).as_posix(),
                           "compare_version": version["compare_version"],
                           "figures": [], "skipped": [], "unmatched": [], "moved_aside": []}

    names = sorted({m for m, _ in found})
    order = {m: {} for m in names}
    for m, q in found:
        order[m].setdefault(q, len(order[m]))
    for m in names:
        if method not in (None, m):
            continue
        key = _sort_key(order[m])
        for quantity in sorted(order[m], key=key):
            points = found[(m, quantity)]
            if not _wanted_by(using, m, quantity):
                continue
            n_samples = len({p["sample"] for p in points})
            units = {p["unit"] for p in points}
            if n_samples < MIN_SAMPLES:
                out["skipped"].append({"method": m, "quantity": quantity,
                                       "reason": f"only {n_samples} sample"})
                continue
            if len(units) > 1:
                out["skipped"].append({"method": m, "quantity": quantity, "reason":
                                       "different units: " + ", ".join(sorted(units))})
                continue
            out["figures"].append(_figure(config, base, stamp, figure_spec(
                m, quantity, points, styles, plot), version, sources))

    # settings that name something no measurement offers
    for m in (m for m in methods if method in (None, m)):
        for setting in using.values():
            wanted = setting["compare"]
            names_asked = wanted.get(m) if isinstance(wanted, dict) else None
            for name in names_asked if isinstance(names_asked, list) else []:
                if not any(fnmatchcase(q, name) for mm, q in found if mm == m) and not any(
                        u["quantity"] == name for u in out["unmatched"]):
                    out["unmatched"].append({"method": m, "quantity": name,
                                             "reason": "no measurement offers it"})

    # figures of quantities that no longer exist (or no longer have 2 samples)
    current = {(f["method"], folder_name(f["quantity"])) for f in out["figures"]}
    alive = {(m, folder_name(q)) for (m, q), pts in found.items()
             if len({p["sample"] for p in pts}) >= MIN_SAMPLES}
    if base.exists():
        for method_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name[0] not in "._"):
            if method not in (None, method_dir.name):
                continue
            for figure_dir in sorted(p for p in method_dir.iterdir() if p.is_dir()):
                if (method_dir.name, figure_dir.name) in alive | current:
                    continue
                target = _aside(base, stamp, method_dir.name, figure_dir.name)
                _move(figure_dir, target, config)
                out["moved_aside"].append({
                    "method": method_dir.name, "quantity": figure_dir.name,
                    "reason": "no longer has measurements of 2 or more samples",
                    "to": target.relative_to(PROJECT_ROOT).as_posix()})
    return out


def _figure(config: dict, base: Path, stamp: str, spec: dict[str, Any],
            version: dict[str, Any], sources: list) -> dict[str, Any]:
    """One figure: kept when its recorded inputs are the current ones, else (re)drawn."""
    final = base / spec["method"] / folder_name(spec["quantity"])
    digest = inputs_sha256(spec, version["compare_version"])
    old = _read(final / "provenance.json") if final.exists() else None
    info = {"method": spec["method"], "quantity": spec["quantity"], "unit": spec["unit"],
            "samples": len(spec["samples"]), "measurements": len(spec["points"]),
            "without_sigma": sum(1 for p in spec["points"] if p["sigma"] is None),
            "caption": caption(spec),
            "dir": final.relative_to(PROJECT_ROOT).as_posix()}
    if old and old.get("inputs_sha256") == digest and _intact(final, old):
        return info | {"status": "kept", "png": f"{info['dir']}/plot.png",
                       "provenance": f"{info['dir']}/provenance.json"}
    staging = base / f".staging-{stamp}-{spec['method']}-{folder_name(spec['quantity'])}"
    assert_pipeline_writable(staging, config)
    staging.mkdir(parents=True, exist_ok=True)
    drawn = render(spec, staging / "plot")
    record = _record(spec, staging, drawn["files"], drawn["font"], digest, version,
                     info["dir"], sources)
    atomic_write_text(staging / "provenance.json",
                      json.dumps(record, indent=1, ensure_ascii=False) + "\n", config)
    status = "drawn"
    if final.exists():
        status = "redrawn"
        _move(final, _aside(base, stamp, spec["method"], final.name), config)
    _move(staging, final, config)
    return info | {"status": status, "png": f"{info['dir']}/plot.png",
                   "provenance": f"{info['dir']}/provenance.json"}


# --- replay ------------------------------------------------------------------------------------

def replay(provenance_path: Path, config: dict, *, force: bool = False) -> dict[str, Any]:
    """Redraw a comparison figure from its provenance.json alone into logs/replay/<stamp>/out/
    and compare the output hashes. Needs the same compare_version (a different commit is only
    noted); a recorded results.json that changed since is noted, not a failure."""
    record = json.loads(provenance_path.read_text(encoding="utf-8"))
    problems, notes = [], []
    now, recorded = versions.compare_version(), record["code"]
    if recorded.get("compare_version") != now["compare_version"]:
        problems.append(f"compare version differs: recorded {recorded.get('compare_version')}, "
                        f"current {now['compare_version']}")
    commit = git_info()["commit"]
    if commit != recorded.get("commit"):
        notes.append(f"made with commit {recorded.get('commit')}, now {commit}; the compare "
                     f"version is the same")
    if problems and not force:
        return {"error": "replay refused", "problems": problems,
                "hint": "use the code that made it (its release or commit), or pass --force to "
                        "compare anyway"}
    for path in sorted({p["results_path"] for p in record["points"]}):
        target = PROJECT_ROOT / path
        if not target.exists():
            notes.append(f"{path} is no longer there; the figure is drawn from the recorded values")
        elif sha256_file(target) != next(p["results_sha256"] for p in record["points"]
                                         if p["results_path"] == path):
            notes.append(f"{path} has changed since the figure was made; the figure is drawn "
                         f"from the recorded values")
    work = PROJECT_ROOT / config["logs_dir"] / "replay" / _stamp()
    assert_pipeline_writable(work, config)
    (work / "out").mkdir(parents=True, exist_ok=True)
    render(record, work / "out" / "plot")
    comparison = {}
    for name, recorded_hash in sorted(record["outputs"].items()):
        regenerated = sha256_file(work / "out" / name)
        comparison[name] = {"recorded": recorded_hash, "regenerated": regenerated,
                            "identical": recorded_hash == regenerated}
    return {"replay_dir": work.relative_to(PROJECT_ROOT).as_posix(), "problems": problems,
            "notes": notes, "comparison": comparison,
            "all_outputs_identical": all(c["identical"] for c in comparison.values())}
