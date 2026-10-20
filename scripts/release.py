"""Make a release (maintainer tool): changelog, version, one commit; prints the rest.

    python scripts/release.py X.Y.Z [--dry-run]

Refuses unless the tree is clean, the branch is main, "Unreleased" in CHANGELOG.md is not empty and
X.Y.Z is greater than the current version. Then it moves Unreleased under `## vX.Y.Z — <today>`
(a new empty Unreleased), sets `__version__` and pyproject's `version`, and commits
"Release vX.Y.Z". It never tags, pushes or publishes: it PRINTS those commands for you to run.
--dry-run shows the changelog change and changes nothing. docs/RELEASING.md has the whole flow.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from labpipe import __version__, release   # noqa: E402

EMPTY_UNRELEASED = "## Unreleased\n\n" + "".join(f"### {p}\n\n" for p in release.PARTS)


def release_changelog(text: str, version: str, today: str) -> tuple[str, str]:
    """(the new CHANGELOG.md text, the notes of the release): Unreleased renamed to
    `## vX.Y.Z — today`, parts without bullets dropped, a new empty Unreleased on top.
    ReleaseError if Unreleased has no bullets or the version is not newer than the last entry."""
    entries = release.parse_changelog(text)
    unreleased = entries[0]["sections"]
    if not any(unreleased.values()):
        raise release.ReleaseError("Unreleased in CHANGELOG.md has no entries: nothing to release")
    last = next((e["version"] for e in entries[1:]), None)
    if last and release.parse_version(version) <= release.parse_version(last):
        raise release.ReleaseError(f"v{version.lstrip('v')} is not newer than {last} in CHANGELOG.md")
    notes = "".join(f"### {part}\n\n" + "".join(f"- {b}\n" for b in unreleased[part]) + "\n"
                    for part in release.PARTS if unreleased[part]).rstrip() + "\n"
    start = text.index("## Unreleased")
    end = text.index("\n## v", start) + 1 if "\n## v" in text[start:] else len(text)
    heading = f"## v{version.lstrip('v')} — {today}\n\n"
    new = text[:start] + EMPTY_UNRELEASED + heading + notes + ("\n" + text[end:] if end < len(text) else "")
    release.parse_changelog(new)                     # the result must parse too
    return new, notes


def bump(text: str, pattern: str, version: str) -> str:
    new, count = re.subn(pattern, rf"\g<1>{version}\g<2>", text, count=1, flags=re.M)
    if count != 1:
        raise release.ReleaseError(f"could not find the version to change with {pattern!r}")
    return new


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("version", help="X.Y.Z")
    parser.add_argument("--dry-run", action="store_true", help="show the change, change nothing")
    args = parser.parse_args(argv)
    try:
        version = ".".join(map(str, release.parse_version(args.version)))
        if release.parse_version(version) <= release.parse_version(__version__):
            raise release.ReleaseError(f"{version} is not greater than the current version {__version__}")
        if not args.dry_run:
            if _git("status", "--porcelain"):
                raise release.ReleaseError("the working tree is not clean: commit or stash first")
            if _git("branch", "--show-current") != "main":
                raise release.ReleaseError("not on main: a release is made from main")
        changelog = ROOT / "CHANGELOG.md"
        old = changelog.read_text(encoding="utf-8")
        new, notes = release_changelog(old, version, datetime.date.today().isoformat())
    except (release.ReleaseError, ValueError) as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                   "CHANGELOG.md", "CHANGELOG.md (release)"))
        print(f"\n(dry run) also: __version__ and pyproject version {__version__} -> {version}")
        return 0
    changelog.write_text(new, encoding="utf-8", newline="\n")
    for path, pattern in (("src/labpipe/__init__.py", r'^(__version__ = ")[^"]*(")'),
                          ("pyproject.toml", r'^(version = ")[^"]*(")')):
        file = ROOT / path
        file.write_text(bump(file.read_text(encoding="utf-8"), pattern, version),
                        encoding="utf-8", newline="\n")
    _git("add", "CHANGELOG.md", "src/labpipe/__init__.py", "pyproject.toml")
    _git("commit", "-m", f"Release v{version}")
    print(f"Committed 'Release v{version}'. Nothing is tagged or pushed. Run:\n\n"
          f"git tag -a v{version} -m \"labpipe v{version}\"\n"
          f"git push origin main v{version}\n"
          f"gh release create v{version} --title \"labpipe v{version}\" --notes-file - <<'EOF'\n"
          f"{notes}EOF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
