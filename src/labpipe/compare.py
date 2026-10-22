"""`labpipe compare`: figures across samples, drawn by code from the analyses' results.

Two sources of figures, both described by a *spec* (a plain dict that holds everything drawn):

- the quantities every method names (`quantities(results)` of its summary module, see
  `labpipe dataset`): a `dots` figure for each one that at least two samples have, the samples
  down the vertical axis in the report's sample order, one point per measurement (nothing
  averaged), σ as an error bar where the analysis gives one, an optional `nominal` as a grey
  diamond in the same row;
- the method's own figures: `compare: <module>` in methods.yaml; `figures(measurements, context)`
  returns specs of the kinds in `labpipe.comparekinds` (dots, lines, bars, images). A module may
  also set `COVERS`, a list of quantity globs its figures replace: those dot plots are then left
  out of the default `all` (they are still drawn when the setting names them).

    analysis/_compare/<method>/<name>/plot.png, plot.pdf, figure.html, provenance.json

`figure.html` is the interactive figure of the report (an inline SVG; plot.png/plot.pdf are the
static ones for papers). `provenance.json` holds the spec itself (values copied from results.json
or read from a raw file read-only, with the source path and sha256), the resolved plot style, the
code (commit and `compare_version`: this module, the plotting modules, `labpipe.comparekinds` and
each method's compare module; no analysis or plot version of a method includes them) and the hash
of every output. `labpipe replay` redraws all outputs from that record alone. A figure is
redrawn only when its recorded inputs (spec, style, compare_version) differ from the current
ones; the old one is moved to analysis/_compare/_superseded/<stamp>/ (nothing is deleted), and so
is the folder of a figure that no longer exists. A hook that fails, or a figure that cannot be
drawn, is recorded as skipped with its reason and the run goes on. matplotlib is imported only
when something is drawn.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import shlex
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from labpipe import comparekinds as kinds
from labpipe import dataset
from labpipe import manifest as mf
from labpipe import settings as settings_mod
from labpipe import versions
from labpipe.provenance import (canonical_json, environment, git_info, runtime, sha256_bytes,
                                sha256_file, utc_now)
from labpipe.registry import load_methods
from labpipe.report import HOOK_ITEM, compare_wanted, report_settings
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, atomic_write_text

SCHEMA = "labpipe.compare-figure/2"
ENTRY_POINT = "labpipe.compare.render"
FOLDER = "_compare"
MIN_SAMPLES = 2
HTML = "figure.html"
WITH_RESULTS = dataset.WITH_RESULTS

# The same values as config/plots.yaml → compare (a test keeps them equal); used for a
# provenance record that has none.
DEFAULT_STYLE = {
    "width_in": "single", "height_per_sample_in": 0.3, "min_height_in": 1.6,
    "palette": "okabe-ito", "marker_size": 4.5, "point_offset": 0.2,
}


def _stamp() -> str:
    return utc_now().replace(":", "").replace("-", "")


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def folder_name(name: str) -> str:
    """The folder of a figure: its name, with characters unsafe in a path replaced."""
    return re.sub(r"[^A-Za-z0-9_.+-]+", "-", name)


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


def offered(ds: dict[str, Any]) -> tuple[dict[tuple[str, str], list[dict[str, Any]]],
                                         dict[tuple[str, str], str]]:
    """({(method, quantity): points}, {(method, quantity): axis label}): every measurement's
    value of every named quantity, in the sample order of the dataset, then by measurement.
    Values that are not finite are left out; `nominal` is copied when the quantity has one."""
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    labels: dict[tuple[str, str], str] = {}
    hashes: dict[str, str] = {}
    for sample, block in ds["samples"].items():
        for item in block["measurements"]:
            path = f"{item['output_dir']}/results.json" if item["output_dir"] else None
            for quantity, value in (item.get("quantities") or {}).items():
                if not _number(value.get("value")) or path is None:
                    continue
                if path not in hashes:
                    hashes[path] = sha256_file(PROJECT_ROOT / path)
                sigma, nominal = value.get("sigma"), value.get("nominal")
                found.setdefault((item["method"], quantity), []).append({
                    "sample": sample, "entry_id": item["entry_id"],
                    "measurement_id": item["measurement_id"], "value": value["value"],
                    "sigma": sigma if _number(sigma) else None,
                    "nominal": nominal if _number(nominal) else None,
                    "unit": value.get("unit") or "", "status": item["status"],
                    "results_path": path, "results_sha256": hashes[path],
                    "key": value.get("key")})
                if value.get("label"):
                    labels.setdefault((item["method"], quantity), value["label"])
    return found, labels


def quantity_spec(method: str, quantity: str, points: list[dict[str, Any]],
                  label: str | None) -> dict[str, Any]:
    """The `dots` figure of a quantity."""
    return {"kind": "dots", "method": method, "name": quantity, "title": label or quantity,
            "label": label, "unit": points[0]["unit"],
            "samples": list(dict.fromkeys(p["sample"] for p in points)), "points": points}


def inputs_sha256(spec: dict[str, Any], compare_version: str) -> str:
    """What decides whether a figure is current: the spec (data, order, plot style) and the code."""
    return sha256_bytes(canonical_json({"spec": spec, "compare_version": compare_version}))


def caption(spec: dict[str, Any]) -> str:
    """What the figure shows; the report's caption and alt text (a spec may bring its own)."""
    return spec.get("caption") or kinds.module(spec["kind"]).caption(spec)


def fig_id(spec: dict[str, Any]) -> str:
    return f"cf-{spec['method']}-{folder_name(spec['name'])}"


def sources(spec: dict[str, Any]) -> dict[str, str]:
    """{path: sha256} of every file the spec says its data came from (results.json, a raw
    file, an image): any dict with `results_path`/`results_sha256` or `path`/`sha256`."""
    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for path_key, hash_key in (("results_path", "results_sha256"), ("path", "sha256")):
                if isinstance(node.get(path_key), str) and isinstance(node.get(hash_key), str):
                    found.setdefault(node[path_key], node[hash_key])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(spec)
    return found


# --- the methods' own figures ------------------------------------------------------------------

def _raw_path(config: dict, entry: dict[str, Any]) -> str | None:
    """The absolute path of the entry's raw file, when it resolves here (read-only: it is only
    handed to the hook, which must not write)."""
    for root in config.get("raw_roots") or []:
        if root.get("id") == entry.get("raw_root") and entry.get("relative_path"):
            path = Path(root["path"]) / entry["relative_path"]
            return str(path) if path.is_file() else None
    return None


def measurements_of(config: dict, method: str, sample_order: list[str]) -> list[dict[str, Any]]:
    """What a method's hook gets: one dict per analysed measurement, in the report's sample order
    (then measurement id): sample, entry_id, measurement_id, status, results (the loaded dict),
    results_path, results_sha256, output_dir, input (results["input"] or None), raw_path."""
    rank = {s: i for i, s in enumerate(sample_order)}
    items = []
    for entry in mf.load(config)["entries"].values():
        folder = entry.get("output_dir")
        if (entry.get("method") != method or entry.get("status") not in WITH_RESULTS
                or not folder or not (PROJECT_ROOT / folder / "results.json").is_file()):
            continue
        path = PROJECT_ROOT / folder / "results.json"
        results = json.loads(path.read_text(encoding="utf-8"))
        items.append({
            "sample": entry.get("sample_id") or "?", "entry_id": entry["entry_id"],
            "measurement_id": entry["measurement_id"], "status": entry["status"],
            "results": results, "results_path": f"{folder}/results.json",
            "results_sha256": sha256_file(path), "output_dir": folder,
            "input": results.get("input"), "raw_path": _raw_path(config, entry)})
    return sorted(items, key=lambda m: (rank.get(m["sample"], len(rank)), m["sample"],
                                        m["measurement_id"], m["entry_id"]))


def _hook(spec: dict[str, Any]):
    return importlib.import_module(spec["compare"]) if spec.get("compare") else None


def _method_config(method: str) -> dict[str, Any]:
    try:
        return settings_mod.resolve(method, None, None)[0]
    except Exception:         # a hook that needs it says so itself
        return {}


def _valid(spec: Any, method: str, plot: dict[str, Any]) -> dict[str, Any]:
    """A hook's figure as the framework records it: a JSON copy with its method and the plot
    style, checked by its kind. Raises ValueError saying what is wrong."""
    if not isinstance(spec, dict):
        raise ValueError(f"a figure must be a dict, not {type(spec).__name__}")
    name = spec.get("name")
    if not isinstance(name, str) or not name or folder_name(name) != name:
        raise ValueError(f"figure name {name!r}: letters, digits and _ . + - only")
    if spec.get("method", method) != method:
        raise ValueError(f"{name}: method is {spec['method']!r}, not {method!r}")
    try:
        spec = json.loads(json.dumps(spec, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: the spec is not plain JSON ({exc})") from exc
    spec["method"], spec["plot"] = method, plot
    kinds.module(spec.get("kind", ""))
    kinds.module(spec["kind"]).check(spec)
    return spec


# --- drawing -----------------------------------------------------------------------------------

def render(spec: dict[str, Any], stem: Path) -> dict[str, Any]:
    """Draw a figure: stem.png (+ the other formats of the plot style) by its kind, and
    figure.html beside them. Deterministic: the same spec gives the same bytes."""
    kind = kinds.module(spec["kind"])
    drawn = kind.render(spec, stem)
    (stem.parent / HTML).write_text(kind.html(spec, fig_id(spec)) + "\n", encoding="utf-8")
    return {"font": drawn["font"], "files": [*drawn["files"], HTML]}


# --- build ---------------------------------------------------------------------------------------

def _record(spec: dict[str, Any], folder: Path, files: list[str], font: dict[str, str],
            digest: str, version: dict[str, Any], where: str, plot_sources: list) -> dict[str, Any]:
    """provenance.json of a figure drawn into `folder` (it will be at `where`)."""
    outputs = {name: sha256_file(folder / name) for name in sorted(files)}
    return {
        "schema": SCHEMA, "created_utc": utc_now(), "method": spec["method"],
        "name": spec["name"], "kind": spec["kind"], "title": spec.get("title"),
        "caption": caption(spec), "inputs_sha256": digest, "spec": spec,
        "plot_sources": plot_sources,
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


def _wanted_by(reports: dict[str, dict], method: str, name: str, covered: bool) -> bool:
    return any(compare_wanted(s["compare"], method, name, covered) for s in reports.values())


def build(config: dict, *, method: str | None = None) -> dict[str, Any]:
    """(Re)build the comparison figures. Which: what the reports with a `compare` section ask
    for (all reports when none has it). `method`: only that method's figures. Returns the
    figures (drawn, redrawn or kept), what was skipped and why, the settings' names nothing
    offers, the folders moved aside and the sample order."""
    methods = load_methods()
    if method is not None and method not in methods:
        raise ValueError(f"unknown method {method!r}; known: {', '.join(sorted(methods))}")
    reports = report_settings(methods)
    using = {k: v for k, v in reports.items() if "compare" in v["sections"]} or reports
    ds = dataset.build(config)
    found, labels = offered(ds)
    plot, plot_sources = settings_mod.plot_style()
    version = versions.compare_version()
    stamp, base = _stamp(), base_dir(config)
    out: dict[str, Any] = {"dir": base.relative_to(PROJECT_ROOT).as_posix(),
                           "compare_version": version["compare_version"],
                           "sample_order": list(ds["samples"]),
                           "figures": [], "skipped": [], "unmatched": [], "moved_aside": []}
    available: dict[str, set[str]] = {}     # per method: every name a setting may name
    alive: set[tuple[str, str]] = set()     # figure folders that still have a reason to exist
    uncertain: set[str] = set()             # methods whose hook failed: their folders stay

    def skip(m: str, name: str, reason: str, covered: bool = False) -> None:
        out["skipped"].append({"method": m, "name": name, "reason": reason, "covered": covered})

    quantity_names = {m for m, _ in found}
    for m in sorted(quantity_names | {n for n, s in methods.items() if s.get("compare")}):
        if method not in (None, m):
            continue
        available[m] = {q for mm, q in found if mm == m}
        planned: list[tuple[dict[str, Any], bool]] = []          # (spec, covered)
        covers: list[str] = []
        try:
            hook = _hook(methods[m])
            if hook is not None:
                covers = list(getattr(hook, "COVERS", None) or [])
                made = hook.figures(measurements_of(config, m, out["sample_order"]),
                                    {"plot": plot, "config": _method_config(m)})
                for item in made or []:
                    try:
                        spec = _valid(item, m, plot)
                    except ValueError as exc:
                        name = item.get("name", "?") if isinstance(item, dict) else "?"
                        skip(m, str(name), str(exc))
                        continue
                    if any(spec["name"] == p["name"] for p, _ in planned):
                        skip(m, spec["name"], "another figure of this method has this name")
                        continue
                    planned.append((spec, False))
                    available[m].add(spec["name"])
        except Exception as exc:        # a failing hook: said, and the quantities still come
            uncertain.add(m)
            skip(m, HOOK_ITEM, f"{type(exc).__name__}: {exc}")
        order: dict[str, int] = {}
        for mm, q in found:
            if mm == m:
                order.setdefault(q, len(order))
        for quantity in sorted(order, key=_sort_key(order)):
            points = found[(m, quantity)]
            covered = any(fnmatchcase(quantity, glob) for glob in covers)
            if not _wanted_by(using, m, quantity, covered):
                continue
            n_samples, units = len({p["sample"] for p in points}), {p["unit"] for p in points}
            if n_samples < MIN_SAMPLES:
                skip(m, quantity, f"only {n_samples} sample", covered)
            elif len(units) > 1:
                skip(m, quantity, "different units: " + ", ".join(sorted(units)), covered)
            elif any(quantity == p["name"] for p, _ in planned):
                skip(m, quantity, "a figure of the method has this name", covered)
            else:
                spec = quantity_spec(m, quantity, points, labels.get((m, quantity)))
                planned.append(({**spec, "plot": plot}, covered))
        for spec, covered in planned:
            if not _wanted_by(using, m, spec["name"], covered):
                alive.add((m, folder_name(spec["name"])))
                continue
            try:
                out["figures"].append(_figure(config, base, stamp, spec, covered, version,
                                              plot_sources))
            except Exception as exc:        # one figure that cannot be drawn does not stop the rest
                skip(m, spec["name"], f"could not be drawn: {type(exc).__name__}: {exc}", covered)
            alive.add((m, folder_name(spec["name"])))
        alive |= {(m, folder_name(q)) for (mm, q), pts in found.items()
                  if mm == m and len({p["sample"] for p in pts}) >= MIN_SAMPLES}

    # settings that name something no measurement and no figure offers
    for m in (m for m in methods if method in (None, m) and m not in uncertain):
        for setting in using.values():
            wanted = setting["compare"]
            names_asked = wanted.get(m) if isinstance(wanted, dict) else None
            for name in names_asked if isinstance(names_asked, list) else []:
                if not any(fnmatchcase(n, name) for n in available.get(m, ())) and not any(
                        u["name"] == name and u["method"] == m for u in out["unmatched"]):
                    out["unmatched"].append({"method": m, "name": name,
                                             "reason": "no measurement offers it"})

    # figures that no longer exist (or no longer have 2 samples)
    if base.exists():
        for method_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name[0] not in "._"):
            if method not in (None, method_dir.name) or method_dir.name in uncertain:
                continue
            for figure_dir in sorted(p for p in method_dir.iterdir() if p.is_dir()):
                if (method_dir.name, figure_dir.name) in alive:
                    continue
                target = _aside(base, stamp, method_dir.name, figure_dir.name)
                _move(figure_dir, target, config)
                out["moved_aside"].append({
                    "method": method_dir.name, "name": figure_dir.name,
                    "reason": "no longer offered (no measurements of 2 or more samples, or no "
                              "such figure)",
                    "to": target.relative_to(PROJECT_ROOT).as_posix()})
    return out


def _figure(config: dict, base: Path, stamp: str, spec: dict[str, Any], covered: bool,
            version: dict[str, Any], plot_sources: list) -> dict[str, Any]:
    """One figure: kept when its recorded inputs are the current ones, else (re)drawn."""
    final = base / spec["method"] / folder_name(spec["name"])
    digest = inputs_sha256(spec, version["compare_version"])
    old = _read(final / "provenance.json") if final.exists() else None
    kind = kinds.module(spec["kind"])
    pairs = kind.inventory(spec)
    info = {"method": spec["method"], "name": spec["name"], "kind": spec["kind"],
            "title": spec.get("title") or spec["name"], "unit": spec.get("unit", ""),
            "samples": kinds.samples_of(pairs), "measurements": len(pairs),
            "without_sigma": sum(1 for p in spec.get("points") or [] if p.get("sigma") is None),
            "covered": covered, "caption": caption(spec),
            "dir": final.relative_to(PROJECT_ROOT).as_posix()}

    def done(status: str, files: list[str]) -> dict[str, Any]:
        return info | {"status": status, "files": sorted(files), "png": f"{info['dir']}/plot.png",
                       "provenance": f"{info['dir']}/provenance.json"}
    if old and old.get("inputs_sha256") == digest and _intact(final, old):
        return done("kept", [*old["outputs"], "provenance.json"])
    staging = base / f".staging-{stamp}-{spec['method']}-{folder_name(spec['name'])}"
    assert_pipeline_writable(staging, config)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        drawn = render(spec, staging / "plot")
        record = _record(spec, staging, drawn["files"], drawn["font"], digest, version,
                         info["dir"], plot_sources)
        atomic_write_text(staging / "provenance.json",
                          json.dumps(record, indent=1, ensure_ascii=False) + "\n", config)
    except Exception:
        _move(staging, _aside(base, stamp, spec["method"], f"_failed-{final.name}"), config)
        raise
    status = "drawn"
    if final.exists():
        status = "redrawn"
        _move(final, _aside(base, stamp, spec["method"], final.name), config)
    _move(staging, final, config)
    return done(status, drawn["files"] + ["provenance.json"])


# --- replay ------------------------------------------------------------------------------------

def replay(provenance_path: Path, config: dict, *, force: bool = False) -> dict[str, Any]:
    """Redraw a comparison figure from its provenance.json alone into logs/replay/<stamp>/out/
    and compare the hashes of all its outputs. Needs the same compare_version (a different commit
    is only noted); a recorded source (results.json, raw or image file) that changed since is
    noted, not a failure."""
    record = json.loads(provenance_path.read_text(encoding="utf-8"))
    problems, notes = [], []
    if record.get("schema") != SCHEMA:
        return {"error": "replay refused", "problems": [
            f"{record.get('schema')} is an older record; `labpipe compare` draws it again"]}
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
    for path, digest in sorted(sources(record["spec"]).items()):
        target = PROJECT_ROOT / path
        if not target.exists():
            notes.append(f"{path} is no longer there; the figure is drawn from the recorded values")
        elif sha256_file(target) != digest:
            notes.append(f"{path} has changed since the figure was made; the figure is drawn "
                         f"from the recorded values")
    work = PROJECT_ROOT / config["logs_dir"] / "replay" / _stamp()
    assert_pipeline_writable(work, config)
    (work / "out").mkdir(parents=True, exist_ok=True)
    render(record["spec"], work / "out" / "plot")
    comparison = {}
    for name, recorded_hash in sorted(record["outputs"].items()):
        regenerated = sha256_file(work / "out" / name)
        comparison[name] = {"recorded": recorded_hash, "regenerated": regenerated,
                            "identical": recorded_hash == regenerated}
    return {"replay_dir": work.relative_to(PROJECT_ROOT).as_posix(), "problems": problems,
            "notes": notes, "comparison": comparison,
            "all_outputs_identical": all(c["identical"] for c in comparison.values())}
