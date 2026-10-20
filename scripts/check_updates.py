"""Newer versions of the pinned dependencies (dependabot cannot read requirements.lock).

    python scripts/check_updates.py [--apply]

Reads the pins in container/requirements.lock and requirements.lock (the same packages, the same
versions), the `analysis` and `refbuild` extras of pyproject.toml, and CLAUDE_CODE_VERSION in
container/versions.env. It asks PyPI (pypi.org/pypi/<name>/json) and the npm registry
(@anthropic-ai/claude-code) for the latest version of each and prints old -> new. With --apply it
rewrites every place consistently; it never installs anything. The weekly workflow
(.github/workflows/updates.yml) runs it with --apply and opens a pull request: updates reach users
only through a release (docs/RELEASING.md). pymatgen stays out of both lock files.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ("container/requirements.lock", "requirements.lock")
PYPROJECT = "pyproject.toml"
VERSIONS_ENV = "container/versions.env"
NPM_PACKAGE = "@anthropic-ai/claude-code"
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s#;]+)", re.M)
PYPROJECT_PIN = re.compile(r'(?<=")([A-Za-z0-9][A-Za-z0-9._-]*)==([^"\s]+)(?=")')


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(text: str, pattern: re.Pattern = PIN) -> dict[str, str]:
    """{package: version} of the `name==version` pins in a lock file (or, with PYPROJECT_PIN,
    in pyproject.toml); comments are skipped."""
    return {_norm(m.group(1)): m.group(2) for m in pattern.finditer(text)
            if not text[text.rfind("\n", 0, m.start()) + 1:m.start()].lstrip().startswith("#")}


def claude_code_version(text: str) -> str:
    match = re.search(r"^CLAUDE_CODE_VERSION=(\S+)", text, re.M)
    if not match:
        raise ValueError(f"CLAUDE_CODE_VERSION not found in {VERSIONS_ENV}")
    return match.group(1)


def is_final(version: str) -> bool:
    """A plain release: digits and dots (and a post release); no rc, dev, beta, alpha."""
    return re.fullmatch(r"\d+(\.\d+)*(\.post\d+)?", version) is not None


def newer(old: str, new: str) -> bool:
    def key(v: str):
        return [int(p) if p.isdigit() else 0 for p in re.split(r"[.]|post", v) if p != ""]
    return key(new) > key(old)


def rewrite(text: str, latest: dict[str, str], pattern: re.Pattern = PIN) -> str:
    """The text with each pin of a package in `latest` set to that version."""
    def sub(m: re.Match) -> str:
        new = latest.get(_norm(m.group(1)))
        return m.group(0) if new is None else f"{m.group(1)}=={new}"
    return pattern.sub(sub, text)


def changes(current: dict[str, str], latest: dict[str, str]) -> dict[str, tuple[str, str]]:
    """{package: (old, new)} where the latest version is newer than the pin."""
    return {n: (v, latest[n]) for n, v in sorted(current.items())
            if n in latest and newer(v, latest[n])}


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:        # noqa: S310 (fixed https URLs)
        return json.load(response)


def latest_pypi(name: str) -> str | None:
    """Latest final release on PyPI (`info.version` is the newest non-prerelease)."""
    version = _get_json(f"https://pypi.org/pypi/{name}/json")["info"]["version"]
    return version if is_final(version) else None


def latest_npm() -> str:
    return _get_json(f"https://registry.npmjs.org/{NPM_PACKAGE}/latest")["version"]


def plan(root: Path, latest_py: dict[str, str], latest_claude: str) -> dict:
    """What to change: {"python": {pkg: (old, new)}, "claude": (old, new) | None} from the files
    in `root` and the given latest versions."""
    lock_pins = [pins((root / f).read_text(encoding="utf-8")) for f in LOCKS]
    if lock_pins[0] != lock_pins[1]:
        diff = sorted(p for p in set(lock_pins[0]) | set(lock_pins[1])
                      if lock_pins[0].get(p) != lock_pins[1].get(p))
        raise ValueError(f"{LOCKS[0]} and {LOCKS[1]} differ in: {', '.join(diff)}; make them identical first")
    current = {**pins((root / PYPROJECT).read_text(encoding="utf-8"), PYPROJECT_PIN), **lock_pins[0]}
    claude = claude_code_version((root / VERSIONS_ENV).read_text(encoding="utf-8"))
    return {"python": changes(current, latest_py),
            "claude": (claude, latest_claude) if newer(claude, latest_claude) else None}


def apply(root: Path, found: dict) -> None:
    latest = {n: new for n, (_, new) in found["python"].items()}
    for name in LOCKS:
        file = root / name
        file.write_text(rewrite(file.read_text(encoding="utf-8"), latest), encoding="utf-8", newline="\n")
    file = root / PYPROJECT
    file.write_text(rewrite(file.read_text(encoding="utf-8"), latest, PYPROJECT_PIN),
                    encoding="utf-8", newline="\n")
    if found["claude"]:
        file = root / VERSIONS_ENV
        file.write_text(re.sub(r"^(CLAUDE_CODE_VERSION=)\S+", rf"\g<1>{found['claude'][1]}",
                               file.read_text(encoding="utf-8"), flags=re.M),
                        encoding="utf-8", newline="\n")


def report(found: dict) -> list[str]:
    lines = [f"{n}: {old} -> {new}" for n, (old, new) in found["python"].items()]
    if found["claude"]:
        lines.append(f"claude-code (CLAUDE_CODE_VERSION): {found['claude'][0]} -> {found['claude'][1]}")
    return lines


def main(argv: list[str]) -> int:
    if set(argv) - {"--apply"}:
        print(__doc__)
        return 2
    names = set(pins((ROOT / LOCKS[0]).read_text(encoding="utf-8"))) | set(
        pins((ROOT / PYPROJECT).read_text(encoding="utf-8"), PYPROJECT_PIN))
    latest = {}
    for name in sorted(names):
        version = latest_pypi(name)
        if version:
            latest[name] = version
    found = plan(ROOT, latest, latest_npm())
    lines = report(found)
    print("\n".join(lines) if lines else "everything is up to date")
    if "--apply" in argv and lines:
        apply(ROOT, found)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
