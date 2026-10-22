"""The lab's own analysis methods: `lab/methods.yaml` and `lab/methods/<name>/` of the project.

Updates replace the code, never the project, so a lab's own method lives in the project
(docs/ADDING_A_METHOD.md, docs/design/install.md):

    lab/methods.yaml            the entries, same schema as the shipped methods.yaml
    lab/methods/<name>/         a Python package: analyze.py, optional summary.py, config.yaml,
                                README.md (its design and settings page)

Its modules are imported as `labpipe_lab.<name>…`. `labpipe_lab` is a package that exists only
at run time: its path is the project's `lab/methods/` folder (`register()`), so nothing is
installed or copied, and the prefix cannot clash with the standard library or with `labpipe`.
Each entry is marked `"lab": True` when loaded (not in the file).
"""

from __future__ import annotations

import importlib
import re
import sys
import types
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import yaml

from labpipe import safety

PREFIX = "labpipe_lab"
REGISTRY_FILE = "lab/methods.yaml"
METHODS_DIR = "lab/methods"
# Not usable as a method name: the names of labpipe's own commands (a test keeps this list
# equal to the commands of the CLI) and the template.
RESERVED = frozenset({
    "agents", "analyze", "automation", "compare", "container", "context", "dataset", "doctor", "guard",
    "init", "inventory", "methods", "migrate", "notes", "onboarding", "pending", "postdoc", "project", "readonly-view",
    "replay", "replot", "report", "requeue", "review", "roots", "scan", "setup", "shell",
    "status", "template", "update", "lab",
})
NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
# Entries of the registry that name a Python module (or module:function).
MODULE_KEYS = ("module", "summary", "gallery", "recognise", "cli_extension", "compare")


class MethodsError(safety.SetupError):
    """lab/methods.yaml is unreadable or breaks a rule; the message says what and where."""


def project_root() -> Path:
    return Path(safety.PROJECT_ROOT)


def methods_dir() -> Path:
    return project_root() / METHODS_DIR


def register() -> None:
    """Make `labpipe_lab.<name>` importable from the current project's lab/methods/ (again,
    if the project changed since: tests, a long-running process)."""
    path = str(methods_dir())
    package = sys.modules.get(PREFIX)
    if package is not None and list(getattr(package, "__path__", [])) == [path]:
        return
    for name in [n for n in sys.modules if n == PREFIX or n.startswith(PREFIX + ".")]:
        del sys.modules[name]
    package = types.ModuleType(PREFIX, "The lab's own methods (the project's lab/methods/).")
    package.__path__ = [path]
    package.__spec__ = ModuleSpec(PREFIX, None, is_package=True)
    package.__spec__.submodule_search_locations = [path]
    sys.modules[PREFIX] = package
    importlib.invalidate_caches()


def is_lab_module(name: str) -> bool:
    return name == PREFIX or name.startswith(PREFIX + ".")


def module_file(name: str) -> Path | None:
    """The source file of a `labpipe_lab.…` module in the project, or None."""
    parts = name.split(".")[1:]
    base = methods_dir().joinpath(*parts)
    for candidate in ((base.with_suffix(".py"), base / "__init__.py") if parts else ()):
        if candidate.is_file():
            return candidate
    return None


def _check(name: str, spec: Any, shipped: dict[str, Any]) -> dict[str, Any]:
    where = f"{REGISTRY_FILE}: method '{name}'"
    if not NAME.match(str(name)):
        raise MethodsError(f"{where}: the name must be lower-case letters, digits and _ "
                           "(e.g. echem2)")
    if name in RESERVED:
        raise MethodsError(f"{where}: '{name}' is the name of a labpipe command; choose another")
    if name in shipped:
        raise MethodsError(f"{where} reuses the name of the shipped method '{name}' (methods.yaml "
                           f"of labpipe and {REGISTRY_FILE} of this project): choose another name")
    if not isinstance(spec, dict):
        raise MethodsError(f"{where}: expected a list of settings (patterns, module, …)")
    for key in ("patterns", "module", "config"):
        if not spec.get(key):
            raise MethodsError(f"{where}: `{key}` is missing")
    if not str(spec["module"]).startswith(f"{PREFIX}.{name}"):
        raise MethodsError(f"{where}: `module` must start with {PREFIX}.{name} "
                           f"(the package lab/methods/{name}/), not {spec['module']!r}")
    for key in MODULE_KEYS[1:]:
        value = spec.get(key)
        if value and not str(value).startswith((f"{PREFIX}.", "labpipe.")):
            raise MethodsError(f"{where}: `{key}` must name a module under {PREFIX}. (or "
                               f"labpipe.), not {value!r}")
    return {**spec, "lab": True}


def load(shipped: dict[str, Any], project: Path | None = None) -> dict[str, dict[str, Any]]:
    """The project's own methods (empty without lab/methods.yaml); `shipped` are the names
    already taken. Raises MethodsError, naming the file, for anything wrong. `project`: only
    to read another project's entries (then nothing becomes importable)."""
    path = Path(project or project_root()) / REGISTRY_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise MethodsError(f"{REGISTRY_FILE} cannot be read: {str(exc).splitlines()[0]}") from exc
    entries = data.get("methods") if isinstance(data, dict) else None
    if entries is None:
        return {}
    if not isinstance(entries, dict):
        raise MethodsError(f"{REGISTRY_FILE}: `methods:` must be a list of named methods "
                           "(name: then its settings, indented)")
    if project is None:
        register()
    return {str(name): _check(str(name), spec, shipped) for name, spec in entries.items()}


def defaults_file(spec: dict[str, Any]) -> Path:
    """Where a method's default settings are: the code's config/ (shipped) or the project's
    (lab methods)."""
    return (project_root() if spec.get("lab") else Path(safety.CODE_ROOT)) / spec["config"]


def lab_spec(method: str) -> dict[str, Any] | None:
    """The entry of a lab method by name (None: not one of the lab's)."""
    path = project_root() / REGISTRY_FILE
    if not path.is_file():
        return None
    try:
        entries = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("methods") or {}
        spec = entries.get(method)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, AttributeError) as exc:
        raise MethodsError(f"{REGISTRY_FILE} cannot be read: {str(exc).splitlines()[0]}") from exc
    return {**spec, "lab": True} if isinstance(spec, dict) and spec.get("config") else None
