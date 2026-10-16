"""methods.yaml: which files belong to which method, subagent and CLI.

A method claims a file when one of its `ignore` or `patterns` globs matches the
path relative to the raw root. If the file type is shared with other methods
(e.g. `.txt`), the method can also name a content check,
`recognise: package.module:function`, called as function(path) -> bool with the
file's full path (it must only read). When it returns False, the next method
is tried. Without a root (e.g. the watcher's cheap snapshot) only globs count.
"""

from __future__ import annotations

import importlib
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml

from labpipe.safety import PROJECT_ROOT


def load_methods() -> dict[str, dict[str, Any]]:
    with open(PROJECT_ROOT / "methods.yaml", encoding="utf-8") as stream:
        return yaml.safe_load(stream)["methods"]


def _matches(relative_path: str, patterns: list[str]) -> bool:
    path = PurePosixPath(relative_path)
    return any(path.full_match(pattern, case_sensitive=False) for pattern in patterns)


@cache
def _recogniser(target: str) -> Callable[[Path], bool]:
    module, _, function = target.partition(":")
    return getattr(importlib.import_module(module), function)


def _recognised(spec: dict[str, Any], root: Path | None, relative_path: str) -> bool:
    if root is None or not spec.get("recognise"):
        return True
    try:
        return bool(_recogniser(spec["recognise"])(Path(root) / relative_path))
    except OSError:
        return False


def classify(relative_path: str, methods: dict[str, dict[str, Any]],
             root: Path | None = None) -> tuple[str | None, str]:
    """Return (method, kind) with kind in {'analyse', 'ignore'}; (None, '') if unclaimed."""
    for name, spec in methods.items():
        if _matches(relative_path, spec.get("ignore", [])):
            if _recognised(spec, root, relative_path):
                return name, "ignore"
        elif _matches(relative_path, spec.get("patterns", [])):
            if _recognised(spec, root, relative_path):
                return name, "analyse"
    return None, ""
