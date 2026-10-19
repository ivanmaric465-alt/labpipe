"""methods.yaml: which files belong to which method, subagent and CLI.

A method claims a file when one of its `ignore` or `patterns` globs matches the
path relative to the raw root. If the file type is shared with other methods
(e.g. `.txt`), the method can also name a content check,
`recognise: package.module:function`, called as function(path) -> bool with the
file's full path (it must only read). When it returns False, the next method
is tried. Without a root (e.g. the watcher's cheap snapshot) only globs count.

Before any method is asked, `skip_reason` leaves out system files
(config/pipeline.yaml → `system_files`, names such as Thumbs.db or `~$x.docx`)
and files the user chose to ignore (config/ignore.yaml, git-ignored): those are
never analysed and never reported as unrecognised.
"""

from __future__ import annotations

import importlib
from fnmatch import fnmatch
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml

from labpipe.safety import CODE_ROOT, PROJECT_ROOT


def load_methods() -> dict[str, dict[str, Any]]:
    with open(CODE_ROOT / "methods.yaml", encoding="utf-8") as stream:
        return yaml.safe_load(stream)["methods"]


USER_IGNORE_FILE = "config/ignore.yaml"


def user_ignore() -> list[str]:
    """config/ignore.yaml → `ignore`: globs relative to a raw root the user chose to ignore."""
    path = PROJECT_ROOT / USER_IGNORE_FILE
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(pattern) for pattern in data.get("ignore") or []]


def skip_reason(relative_path: str, config: dict, ignored: list[str]) -> str | None:
    """Why a file is left out before classification (None: it is not)."""
    name = PurePosixPath(relative_path).name.lower()
    if any(fnmatch(name, pattern.lower()) for pattern in config.get("system_files") or []):
        return "system file"
    if ignored and _matches(relative_path, ignored):
        return f"ignored by you ({USER_IGNORE_FILE})"
    return None


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
    """Return (method, kind) with kind in {'analyse', 'ignore'}; (None, '') if no method claims the file."""
    for name, spec in methods.items():
        if _matches(relative_path, spec.get("ignore", [])):
            if _recognised(spec, root, relative_path):
                return name, "ignore"
        elif _matches(relative_path, spec.get("patterns", [])):
            if _recognised(spec, root, relative_path):
                return name, "analyse"
    return None, ""
