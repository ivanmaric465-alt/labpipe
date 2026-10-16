"""methods.yaml: which files belong to which method, subagent and CLI."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import yaml

from labpipe.safety import PROJECT_ROOT


def load_methods() -> dict[str, dict[str, Any]]:
    with open(PROJECT_ROOT / "methods.yaml", encoding="utf-8") as stream:
        return yaml.safe_load(stream)["methods"]


def _matches(relative_path: str, patterns: list[str]) -> bool:
    path = PurePosixPath(relative_path)
    return any(path.full_match(pattern, case_sensitive=False) for pattern in patterns)


def classify(relative_path: str, methods: dict[str, dict[str, Any]]) -> tuple[str | None, str]:
    """Return (method, kind) with kind in {'analyse', 'ignore'}; (None, '') if unclaimed."""
    for name, spec in methods.items():
        if _matches(relative_path, spec.get("ignore", [])):
            return name, "ignore"
        if _matches(relative_path, spec.get("patterns", [])):
            return name, "analyse"
    return None, ""
