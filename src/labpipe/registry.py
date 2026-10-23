"""methods.yaml: which files belong to which method, subagent and CLI.

A method claims a file when one of its `ignore` or `patterns` globs matches the
path relative to the raw root. If the file type is shared with other methods
(e.g. `.txt`), the method can also name a content check,
`recognise: package.module:function`, called as function(path) -> bool with the
file's full path (it must only read). When it returns False, the next method
is tried. Methods are tried in the order of `load_methods()`: the shipped ones first, then the
lab's own (lab/methods.yaml), so a lab method never takes a file from a shipped one. Without a
root (e.g. the watcher's cheap snapshot) only globs count.

`companions: package.module:function` (optional) names the other files a method reads besides
the main one (the SEM .txt, the images an EDS report links or its settings choose):
function(path, settings) -> list of Paths that exist now, `settings` being the measurement's
resolved settings (it must only read). `input_companions` hashes them; the scanner and the
pipeline compare those hashes, so a companion that changes, appears or disappears makes the
measurement out of date.

Before any method is asked, `skip_reason` leaves out system files
(config/pipeline.yaml → `system_files`, names such as Thumbs.db or `~$x.docx`)
and files the user chose to ignore (config/ignore.yaml, git-ignored): those are
never analysed and never reported as unrecognised.
"""

from __future__ import annotations

import importlib
from fnmatch import fnmatch, fnmatchcase
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml

from labpipe import labmethods, migrations
from labpipe.provenance import sha256_file
from labpipe.safety import CODE_ROOT, PROJECT_ROOT


def shipped_methods() -> dict[str, dict[str, Any]]:
    with open(CODE_ROOT / "methods.yaml", encoding="utf-8") as stream:
        return yaml.safe_load(stream)["methods"]


def load_methods() -> dict[str, dict[str, Any]]:
    """The shipped methods, then the project's own (lab/methods.yaml; `"lab": True`). Raises
    labmethods.MethodsError (a SetupError) for a broken or clashing lab/methods.yaml."""
    shipped = shipped_methods()
    return {**shipped, **labmethods.load(shipped)}


USER_IGNORE_FILE = "config/ignore.yaml"


def user_ignore() -> list[str]:
    """config/ignore.yaml → `ignore`: globs relative to a raw root the user chose to ignore."""
    path = PROJECT_ROOT / USER_IGNORE_FILE
    if not path.exists():
        return []
    data = migrations.upgraded(path, yaml.safe_load(path.read_text(encoding="utf-8")) or {},
                               PROJECT_ROOT)
    return [str(pattern) for pattern in data.get("ignore") or []]


def skip_reason(relative_path: str, config: dict, ignored: list[str]) -> str | None:
    """Why a file is left out before classification (None: it is not)."""
    name = PurePosixPath(relative_path).name.lower()
    if any(fnmatch(name, pattern.lower()) for pattern in config.get("system_files") or []):
        return "system file"
    if ignored and _matches(relative_path, ignored):
        return f"ignored by you ({USER_IGNORE_FILE})"
    return None


def _segments_match(parts: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return not parts
    head, rest = patterns[0], patterns[1:]
    if head == "**":                       # any number of folders (also none)
        if not rest:
            return bool(parts)
        return any(_segments_match(parts[i:], rest) for i in range(len(parts) + 1))
    return bool(parts) and fnmatchcase(parts[0], head) and _segments_match(parts[1:], rest)


def glob_match(relative_path: str, pattern: str) -> bool:
    """Whole-path glob, case-insensitive; `**` matches any number of folders. (Same as
    PurePosixPath.full_match, which Python 3.10-3.12 hosts do not have.)"""
    return _segments_match(PurePosixPath(relative_path.lower()).parts,
                           PurePosixPath(pattern.lower()).parts)


def _matches(relative_path: str, patterns: list[str]) -> bool:
    return any(glob_match(relative_path, pattern) for pattern in patterns)


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


@cache
def _companion_finder(target: str) -> Callable[[Path, dict], list[Path]]:
    module, _, function = target.partition(":")
    return getattr(importlib.import_module(module), function)


def input_companions(spec: dict[str, Any], root: Path, relative_path: str,
                     settings: dict[str, Any]) -> dict[str, str] | None:
    """{path relative to the raw root (posix): sha256} of the companion files the method reads
    for this file with these (resolved) settings; None when the method names none (no
    `companions:`). Only reads."""
    if not spec.get("companions"):
        return None
    found = _companion_finder(spec["companions"])(Path(root) / relative_path, settings)
    return {Path(path).relative_to(root).as_posix(): sha256_file(Path(path))
            for path in sorted(found, key=lambda p: str(p))}
