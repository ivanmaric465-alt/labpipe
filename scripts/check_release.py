"""Fail when a method's calculation changed without a line in CHANGELOG.md (Unreleased).

    python scripts/check_release.py [BASE]      # BASE: git ref to compare with (default origin/main)

1. CHANGELOG.md must parse (labpipe.release.parse_changelog).
2. For every shipped method (methods.yaml) whose analysis version differs between BASE and HEAD,
   "Unreleased → Changes results" needs a bullet starting with the method's name in backticks.
   A method that is new at HEAD needs a bullet naming it in "New" (any part counts, "Changes
   results" included); a removed method needs one in "Changes results".
3. A commit message line `Results-unchanged: <method> <reason>` (since BASE) excuses a method whose
   analysis version changed although the numbers are provably the same (a refactor). What was
   excused is printed.

BASE's versions come from a temporary `git worktree` of BASE, where `labpipe.versions.describe`
runs in a subprocess (LABPIPE_CODE and LABPIPE_PROJECT point at it). Dependency bumps that change
an analysis version are no exception: they need their bullet too.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from labpipe import release   # noqa: E402

LIST_VERSIONS = (
    "import json\n"
    "from labpipe import versions\n"
    "from labpipe.registry import shipped_methods\n"
    "print(json.dumps({m: versions.describe(m)['analysis_version'] for m in shipped_methods()}))\n"
)


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def analysis_versions(code: Path) -> dict[str, str]:
    """Analysis version of every shipped method of the code folder `code` (in a subprocess)."""
    env = {**os.environ, "LABPIPE_CODE": str(code), "LABPIPE_PROJECT": str(code),
           "PYTHONPATH": str(code / "src")}
    env.pop("LABPIPE_ROOT", None)
    run = subprocess.run([sys.executable, "-c", LIST_VERSIONS], cwd=code, env=env,
                         capture_output=True, text=True, encoding="utf-8")
    if run.returncode:
        raise RuntimeError(f"could not compute the analysis versions in {code}:\n{run.stderr}")
    return json.loads(run.stdout.strip().splitlines()[-1])


def base_versions(base: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "base"
        _git("worktree", "add", "--detach", str(tree), base)
        try:
            return analysis_versions(tree)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT,
                           capture_output=True)


def check(old: dict[str, str], new: dict[str, str], unreleased: dict[str, list[str]],
          unchanged: dict[str, str]) -> tuple[list[str], list[str]]:
    """(problems, bypassed) for the analysis versions before (`old`) and after (`new`), the
    parts of "Unreleased" and the `Results-unchanged:` trailers {method: reason}."""
    def named(method: str, parts: tuple[str, ...]) -> bool:
        return any(re.match(rf"`{re.escape(method)}`", b) for p in parts for b in unreleased[p])

    problems, bypassed = [], []
    for method in sorted(set(old) | set(new)):
        if method in old and method in new and old[method] == new[method]:
            continue
        if method in unchanged:
            bypassed.append(f"`{method}`: {unchanged[method]}")
        elif method not in old:
            if not named(method, release.PARTS):
                problems.append(f"method `{method}` is new: add a bullet naming it in backticks "
                                f"to CHANGELOG.md → Unreleased → New")
        elif method not in new:
            if not named(method, ("Changes results",)):
                problems.append(f"method `{method}` was removed: add a bullet "
                                f"``- `{method}`: removed (why).`` to CHANGELOG.md → Unreleased → Changes results")
        elif not named(method, ("Changes results",)):
            problems.append(
                f"the analysis version of `{method}` changed ({old[method]} → {new[method]}): add "
                f"``- `{method}`: what changes in the numbers (why).`` to CHANGELOG.md → Unreleased → "
                f"Changes results; if the numbers are provably the same (a refactor), add the commit "
                f"message line `Results-unchanged: {method} <reason>`")
    return problems, bypassed


def trailers(log: str) -> dict[str, str]:
    found = {}
    for line in log.splitlines():
        if line.lower().startswith("results-unchanged:"):
            parts = line.split(":", 1)[1].split(None, 1)
            if parts:
                found[parts[0].strip("`,")] = parts[1].strip() if len(parts) > 1 else "(no reason given)"
    return found


def main(base: str = "origin/main") -> int:
    try:
        entries = release.parse_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    except release.ReleaseError as exc:
        print(f"changelog: {exc}")
        return 1
    merge_base = _git("merge-base", base, "HEAD").strip()
    unchanged = trailers(_git("log", "--format=%B", f"{merge_base}..HEAD"))
    problems, bypassed = check(base_versions(merge_base), analysis_versions(ROOT),
                               entries[0]["sections"], unchanged)
    for line in bypassed:
        print(f"Results-unchanged (not checked): {line}")
    for line in problems:
        print(line)
    if not problems:
        print(f"release: ok (changelog parses; no unannounced change of an analysis version since {base})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
