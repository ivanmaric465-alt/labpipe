"""Releases: the version, CHANGELOG.md and the release tags of a git checkout.

Host side (Python 3.10, PyYAML at most, no numpy), so `labpipe update` and `labpipe --version`
work without the analysis libraries. The functions are pure where they can be: the changelog
is parsed from text, `between` works on the parsed entries. Only `tags` and `current` run git.

CHANGELOG.md has a fixed format (docs/RELEASING.md): `## Unreleased` first, then
`## vX.Y.Z — YYYY-MM-DD`, newest first; in each only the four `###` parts below, in this order,
with `- ` bullets. In "Changes results" each bullet starts with the method name in backticks.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from labpipe import __version__

PARTS = ("Changes results", "Settings migrated", "New", "Needs your action")
UNRELEASED = "Unreleased"
TAG = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
HEADING = re.compile(r"##\s+(v\d+\.\d+\.\d+)\s+[—-]\s+(\d{4}-\d{2}-\d{2})")


class ReleaseError(Exception):
    """CHANGELOG.md or a version does not follow the rules; the message says what to change."""


def parse_version(text: str) -> tuple[int, int, int]:
    """'v0.2.0' or '0.2.0' -> (0, 2, 0)."""
    match = TAG.fullmatch(str(text).strip())
    if not match:
        raise ValueError(f"not a version (expected vX.Y.Z or X.Y.Z): {text!r}")
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


def parse_changelog(text: str) -> list[dict[str, Any]]:
    """The entries, newest first: {"version": "Unreleased" | "v0.2.0", "date": str | None,
    "sections": {part: [bullet, ...]}} (all four parts present, empty ones []). Raises
    ReleaseError for anything outside the fixed format."""
    entries: list[dict[str, Any]] = []
    part: str | None = None
    last_part = -1
    title = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        where = f"CHANGELOG.md line {number}"
        if line.startswith("### "):
            if not entries:
                raise ReleaseError(f"{where}: `{line}` before any `## ` heading")
            name = line[4:].strip()
            if name not in PARTS:
                raise ReleaseError(f"{where}: unknown part `{line}`; the parts are "
                                   + ", ".join(f"`### {p}`" for p in PARTS))
            index = PARTS.index(name)
            if index <= last_part:
                raise ReleaseError(f"{where}: `### {name}` is {'twice' if index == last_part else 'out of order'} "
                                   f"in {entries[-1]['version']}; the order is " + ", ".join(PARTS))
            part, last_part = name, index
        elif line.startswith("## "):
            if line[3:].strip() == UNRELEASED:
                if entries:
                    raise ReleaseError(f"{where}: `## Unreleased` must be the first entry")
                entries.append({"version": UNRELEASED, "date": None, "sections": {p: [] for p in PARTS}})
            else:
                match = HEADING.fullmatch(line)
                if not match:
                    raise ReleaseError(f"{where}: bad heading `{line}`; use `## vX.Y.Z — YYYY-MM-DD` "
                                       "or `## Unreleased`")
                version = match.group(1)
                if entries and entries[-1]["version"] != UNRELEASED and \
                        parse_version(version) >= parse_version(entries[-1]["version"]):
                    raise ReleaseError(f"{where}: {version} must be older than "
                                       f"{entries[-1]['version']} (newest first)")
                entries.append({"version": version, "date": match.group(2),
                                "sections": {p: [] for p in PARTS}})
            part, last_part = None, -1
        elif line.startswith("# "):
            title = True
        elif line.startswith("- "):
            if part is None:
                raise ReleaseError(f"{where}: a bullet outside a `### ` part: `{line}`")
            if part == "Changes results" and not re.match(r"- `[^`]+`", line):
                raise ReleaseError(f"{where}: a bullet in `Changes results` starts with the method "
                                   f"name in backticks, e.g. ``- `xrd`: what changed (why).``: `{line}`")
            entries[-1]["sections"][part].append(line[2:].strip())
        elif line.startswith(("  ", "\t")) and part is not None and entries[-1]["sections"][part]:
            entries[-1]["sections"][part][-1] += " " + line.strip()      # a wrapped bullet
        elif not entries:
            continue                                   # an introduction under the title
        elif line.strip() and line.strip() != "None.":
            raise ReleaseError(f"{where}: unexpected text `{line.strip()}`; "
                               "only `- ` bullets (or `None.`) belong in a part")
    if not title:
        raise ReleaseError("CHANGELOG.md: starts with `# Changelog`")
    if not entries or entries[0]["version"] != UNRELEASED:
        raise ReleaseError("CHANGELOG.md: the first entry is `## Unreleased`")
    return entries


def between(entries: list[dict[str, Any]], installed: str, target: str) -> list[dict[str, Any]]:
    """The entries after `installed` up to and including `target` (newest first). For a
    downgrade (target older) the entries that would be undone, each with "undone": True.
    "Unreleased" is never part of it. Versions as 'v0.2.0' or '0.2.0'."""
    old, new = parse_version(installed), parse_version(target)
    released = [e for e in entries if e["version"] != UNRELEASED]
    if new >= old:
        return [e for e in released if old < parse_version(e["version"]) <= new]
    return [{**e, "undone": True} for e in released if new < parse_version(e["version"]) <= old]


def _git(code_root: Path | str, *args: str) -> str | None:
    try:
        run = subprocess.run(["git", *args], cwd=str(code_root), capture_output=True,
                             text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return run.stdout if run.returncode == 0 else None


def tags(code_root: Path | str) -> list[str]:
    """Release tags (vX.Y.Z) of a git checkout, oldest first; [] if it is not one."""
    out = _git(code_root, "tag", "--list", "v*")
    found = [t.strip() for t in (out or "").splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t.strip())]
    return sorted(found, key=parse_version)


def current(code_root: Path | str) -> dict[str, Any]:
    """{"version": __version__, "tag": the release tag at HEAD or None, "commit": short sha or
    None (no git checkout), "dirty": uncommitted changes}."""
    commit = (_git(code_root, "rev-parse", "--short", "HEAD") or "").strip() or None
    at_head = (_git(code_root, "tag", "--points-at", "HEAD") or "").split()
    release = sorted((t for t in at_head if re.fullmatch(r"v\d+\.\d+\.\d+", t)), key=parse_version)
    dirty = bool((_git(code_root, "status", "--porcelain") or "").strip()) if commit else False
    return {"version": __version__, "tag": release[-1] if release else None,
            "commit": commit, "dirty": dirty}
