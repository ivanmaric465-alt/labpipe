"""Fail when code changed but none of the docs that belong to it did (docs/docmap.yaml).

    python scripts/check_docs.py [BASE]      # BASE: git ref to compare with (default origin/main)

Compares the files changed since BASE (committed and uncommitted); an area with
`on: added or removed` counts only files added, removed or renamed. An area whose code changed
passes if one of its docs changed too, or if a commit message since BASE has a line
`Docs-checked: <area>[, <area>…]`. Linked docs (docmap → linked) are listed as a reminder.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout


def _matches(path: str, patterns: list[str]) -> bool:
    return any(PurePosixPath(path).full_match(pattern) for pattern in patterns)


def main(base: str = "origin/main") -> int:
    docmap = yaml.safe_load((ROOT / "docs" / "docmap.yaml").read_text(encoding="utf-8"))
    merge_base = _git("merge-base", base, "HEAD").strip()
    changed, added_or_removed = set(), set()
    for line in _git("diff", "--name-status", merge_base).splitlines():
        status, *paths = line.split("\t")
        changed |= set(paths)
        if status[0] in "ADR":
            added_or_removed |= set(paths)
    new = set(_git("ls-files", "--others", "--exclude-standard").splitlines())
    changed |= new
    added_or_removed |= new
    checked = set()
    for line in _git("log", "--format=%B", f"{merge_base}..HEAD").splitlines():
        if line.lower().startswith("docs-checked:"):
            checked |= {a.strip() for a in line.split(":", 1)[1].split(",") if a.strip()}
    missing = []
    for name, area in docmap["areas"].items():
        candidates = added_or_removed if area.get("on") == "added or removed" else changed
        code = sorted(p for p in candidates if _matches(p, area["code"]))
        if code and name not in checked and not any(_matches(p, area["docs"]) for p in changed):
            missing.append((name, code, area["docs"]))
    for name, code, docs in missing:
        print(f"[{name}] code changed: {', '.join(code[:4])}{' …' if len(code) > 4 else ''}\n"
              f"    review and update: {', '.join(docs)}\n"
              f"    (or, if they need no change, a commit message line `Docs-checked: {name}`)")
    for group in docmap.get("linked", []):
        touched = [p for p in changed if _matches(p, group)]
        if touched:
            print(f"reminder: {', '.join(sorted(touched))} changed; check it still agrees with "
                  f"{', '.join(group)}")
    if not missing:
        print(f"docs: ok ({len(changed)} changed file(s) since {base})")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
