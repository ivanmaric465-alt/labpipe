"""Analysis version and plot version per method: what marks an analysis "out of date".

`analysis_version(method)` is a short hash of the source that computes the method's
results.json: the import closure of its `module` in methods.yaml, following `labpipe.*`
imports, without the plot modules and the infrastructure (below), plus the files named in
`analysis_data:` (reference data). `plot_version(method)` is a hash of the method's
`plot_modules:` and `labpipe.plotting`. A change in another method, a document, the report
or an agent changes neither. The git commit stays in provenance for replay and tracing.

Sources are hashed with line endings normalised to \\n, so Linux and Windows agree.
A method that draws inside its analyze module (eds, sem) has its drawing in the analysis
version: it lists no `plot_modules`.
"""

from __future__ import annotations

import ast
import hashlib
from functools import cache
from pathlib import Path
from typing import Any

from labpipe import labmethods, safety
from labpipe.registry import load_methods

# Source root of the labpipe package (the directory holding `labpipe/`). Tests point it at a copy.
SOURCE_ROOT = Path(__file__).resolve().parent.parent

# Modules that do not compute numbers of any method: path checks, records, settings loading
# (the resolved settings are fingerprinted separately), the manifest, locks, the registry.
# Anything that can change a value in a results.json (naming, io, numerics) stays in.
INFRASTRUCTURE = frozenset({
    "labpipe.safety", "labpipe.provenance", "labpipe.settings", "labpipe.manifest",
    "labpipe.locking", "labpipe.registry", "labpipe.versions",
})
# Packages that only group modules: never part of a version themselves.
PACKAGES_SKIPPED = frozenset({"labpipe", "labpipe.methods", labmethods.PREFIX})
PLOTTING = "labpipe.plotting"


def _is_plotting(name: str) -> bool:
    return name == PLOTTING or name.startswith(PLOTTING + ".")


def _own(name: str) -> bool:
    """A module of labpipe or of the lab's own methods (labpipe_lab.…, in the project)."""
    return name.split(".")[0] in ("labpipe", labmethods.PREFIX)


def _module_file(name: str) -> Path | None:
    if labmethods.is_lab_module(name):
        return labmethods.module_file(name)
    base = SOURCE_ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def read_source(name: str) -> bytes:
    """The source of a module, line endings as \\n. (Tests patch SOURCE_ROOT, not this.)"""
    path = _module_file(name)
    if path is None:
        raise FileNotFoundError(f"module {name} not found under "
                                f"{labmethods.methods_dir() if labmethods.is_lab_module(name) else SOURCE_ROOT}")
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _imports(name: str, source: bytes) -> set[str]:
    """Names of the labpipe modules a module imports (anywhere in it, also inside functions)."""
    is_package = (_module_file(name) or Path()).name == "__init__.py"
    package = name if is_package else name.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if _own(a.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            if not _own(module):
                continue
            found.add(module)
            for alias in node.names:       # `from labpipe.methods.xrd import fitting`
                if _module_file(f"{module}.{alias.name}"):
                    found.add(f"{module}.{alias.name}")
    return found


def _ancestors(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def closure(start: str, excluded: set[str]) -> list[str]:
    """Modules the start module needs, sorted, without the excluded ones (and not past them)."""
    def skipped(name: str) -> bool:
        return (name in excluded or name in INFRASTRUCTURE or name in PACKAGES_SKIPPED
                or _is_plotting(name))

    todo, seen = [start], set()
    while todo:
        name = todo.pop()
        if name in seen or skipped(name) or _module_file(name) is None:
            continue
        seen.add(name)
        todo.extend(_imports(name, read_source(name)))
        todo.extend(a for a in _ancestors(name) if _module_file(a))   # package __init__
    return sorted(seen)


def _data_root(pattern: str):
    """Data globs starting with lab/ are the project's own (e.g. its XRD references)."""
    return safety.PROJECT_ROOT if pattern.startswith("lab/") else safety.CODE_ROOT


def _data_files(spec: dict[str, Any]) -> list[str]:
    files = {p.relative_to(_data_root(pattern)).as_posix()
             for pattern in spec.get("analysis_data") or []
             for p in _data_root(pattern).glob(pattern) if p.is_file()}
    return sorted(files)


def _digest(parts: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for label, data in parts:
        digest.update(label.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()[:16]


def _plot_modules(spec: dict[str, Any]) -> list[str]:
    return sorted(spec.get("plot_modules") or [])


def plotting_modules() -> list[str]:
    folder = SOURCE_ROOT / "labpipe" / "plotting"
    return sorted(f"{PLOTTING}.{p.stem}" if p.stem != "__init__" else PLOTTING
                  for p in folder.glob("*.py"))


def describe(method: str, methods: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Both versions of a method and what they are made of."""
    spec = (methods or load_methods())[method]
    plots = _plot_modules(spec)
    analysis = closure(spec["module"], set(plots))
    data = _data_files(spec)
    plot_all = sorted({*plots, *plotting_modules()})
    return {
        "analysis_version": _digest(
            [(n, read_source(n)) for n in analysis]
            + [(f, (_data_root(f) / f).read_bytes().replace(b"\r\n", b"\n")) for f in data]),
        "plot_version": _digest([(n, read_source(n)) for n in plot_all]),
        "analysis_modules": analysis,
        "analysis_data": data,
        "plot_modules": plot_all,
    }


def all_versions(methods: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    methods = methods or load_methods()
    return {name: describe(name, methods) for name in methods}


def current(method: str) -> dict[str, str | None]:
    """{"analysis_version", "plot_version"} of a method; None for a method not in methods.yaml
    (a method under test)."""
    try:
        info = describe(method)
    except (KeyError, FileNotFoundError):
        return {"analysis_version": None, "plot_version": None}
    return {"analysis_version": info["analysis_version"], "plot_version": info["plot_version"]}
