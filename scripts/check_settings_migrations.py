"""Fail when a release removes or renames a settings key without a migration.

    python scripts/check_settings_migrations.py [BASE]      # BASE: git ref to compare with (default origin/main)

Collects the key paths (dotted) of the settings files labpipe ships (config/plots.yaml,
report.yaml, pipeline.yaml, config/*.example.yaml and each shipped method's default file named in
methods.yaml) at BASE and in the working tree. Every key path that was at BASE and is gone now must
be covered by a migration in labpipe.migrations.MIGRATIONS: its `old_keys` names the key (or a
section above it) as the user's file spells it, and its `files` name that file. Added keys need
nothing. A migration of release "Unreleased" must also be mentioned (its id or one of its old
keys) in CHANGELOG.md under "Settings migrated" of the Unreleased entry; skipped without a
CHANGELOG.md. (docs/settings/migrations.md, docs/ARCHITECTURE.md)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from labpipe import migrations  # noqa: E402

FIXED = {"config/plots.yaml": "plots", "config/report.yaml": "report",
         "config/pipeline.yaml": "pipeline"}


def key_paths(data: Any, prefix: str = "") -> set[str]:
    """Every dotted key path of the mappings in `data` (lists and values are leaves)."""
    paths: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}{key}"
            paths.add(path)
            paths |= key_paths(value, path + ".")
    return paths


def _collapse(paths: set[str]) -> set[str]:
    """samples.example.yaml: the sample IDs and preset names are examples, not keys."""
    return {re.sub(r"^(samples|presets)\.[^.]+", r"\1.*", p) for p in paths}


def collect(files: dict[str, str], read: Callable[[str], str | None]) -> dict[str, set[str]]:
    """{file: key paths} of the files (name -> kind), read with `read(path)` (None: absent)."""
    found = {}
    for name, kind in files.items():
        text = read(name)
        if text is None:
            continue
        paths = key_paths(yaml.safe_load(text) or {})
        found[name] = _collapse(paths) if kind == "example:samples" else paths
    return found


def shipped_files(listing: list[str], read: Callable[[str], str | None]) -> dict[str, str]:
    """The shipped settings files among `listing`: {file: kind}."""
    files = {name: kind for name, kind in FIXED.items() if name in listing}
    for name in listing:
        match = re.fullmatch(r"config/([a-z_]+)\.example\.yaml", name)
        if match:
            files[name] = f"example:{match.group(1)}"
    text = read("methods.yaml")
    for method, spec in ((yaml.safe_load(text) or {}).get("methods") or {}).items() if text else []:
        if isinstance(spec, dict) and spec.get("config"):
            files[spec["config"]] = f"method:{method}"
    return files


def user_forms(file: str, kind: str, key: str) -> list[tuple[str, str]]:
    """Where a user's files spell the key `key` of a shipped file: [(file, key path)]."""
    if kind == "plots":
        return [("config/lab.yaml", f"plots.{key}")]
    if kind == "report":
        return [("config/lab.yaml", f"report.{key}")]
    if kind == "pipeline":     # users have no pipeline.yaml in a project; only these are theirs
        if key.split(".")[0] == "container":
            return [("config/local.yaml", key)]
        if key == "settle_minutes":
            return [("config/local.yaml", "automation.settle_minutes")]
        return [("config/pipeline.yaml", key)]
    if kind.startswith("example:"):
        return [(f"config/{kind.split(':')[1]}.yaml", key)]
    method = kind.split(":", 1)[1]
    return [("config/lab.yaml", f"{method}.{key}"),
            ("config/samples.yaml", f"samples.*.{method}.{key}"),
            ("config/samples.yaml", f"presets.*.{method}.{key}"),
            (f"config/measurements/{method}/x.yaml", key)]


def _covers(old_key: str, key: str) -> bool:
    """Is `key` the path `old_key` or below it (`*` matches any one name)?"""
    old, new = old_key.split("."), key.split(".")
    return len(old) <= len(new) and all(a == "*" or a == b for a, b in zip(old, new))


def _covered(file: str, key: str, migration_list: list) -> bool:
    return any(_covers(old, key) and any(migrations._glob_match(file, pattern)
                                         for pattern in m.files)
               for m in migration_list for old in m.old_keys)


def uncovered(base: dict[str, set[str]], head: dict[str, set[str]], kinds: dict[str, str],
              migration_list: list) -> list[dict[str, Any]]:
    """The key paths in `base` and not in `head` that no migration covers."""
    problems = []
    for file, keys in sorted(base.items()):
        for key in sorted(keys - head.get(file, set())):
            forms = user_forms(file, kinds[file], key)
            missing = [(f, k) for f, k in forms if not _covered(f, k, migration_list)]
            if missing:
                problems.append({"file": file, "key": key, "needs": missing})
    return problems


def changelog_problems(migration_list: list, changelog: str | None) -> list[str]:
    """Unreleased migrations that CHANGELOG.md does not mention under "Settings migrated"."""
    if changelog is None:
        return []
    lines = changelog.splitlines()
    section = []
    start = next((i for i, line in enumerate(lines)
                  if re.match(r"#+\s.*unreleased", line, re.I)), None)
    if start is not None:
        level = len(re.match(r"#+", lines[start]).group())
        end = next((i for i in range(start + 1, len(lines))
                    if re.match(rf"#{{1,{level}}}\s", lines[i])), len(lines))
        block = lines[start + 1:end]
        heading = next((i for i, line in enumerate(block)
                        if re.match(r"(#+\s*|\*\*)settings migrated", line.strip(), re.I)), None)
        if heading is not None:
            stop = next((i for i in range(heading + 1, len(block))
                         if re.match(r"#+\s|\*\*[A-Z]", block[i].strip())), len(block))
            section = block[heading + 1:stop]
    text = "\n".join(section)
    return [f"migration {m.id} (Unreleased) is not mentioned in CHANGELOG.md under "
            f"\"Settings migrated\" of the Unreleased entry (name its id or one of: "
            f"{', '.join(m.old_keys) or 'its keys'})"
            for m in migration_list if m.release == migrations.UNRELEASED
            and not (m.id in text or any(key in text for key in m.old_keys))]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout


def main(base: str = "origin/main") -> int:
    try:
        merge_base = _git("merge-base", base, "HEAD").strip()
    except subprocess.CalledProcessError:
        print(f"settings migrations: no {base} to compare with; skipped")
        return 0

    def read_base(path: str) -> str | None:
        try:
            return _git("show", f"{merge_base}:{path}")
        except subprocess.CalledProcessError:
            return None

    def read_head(path: str) -> str | None:
        try:
            return (ROOT / path).read_text(encoding="utf-8")
        except OSError:
            return None

    base_files = shipped_files(_git("ls-tree", "-r", "--name-only", merge_base, "config",
                                    "methods.yaml").split(), read_base)
    head_listing = [p.relative_to(ROOT).as_posix() for p in (ROOT / "config").glob("*.yaml")]
    head_files = shipped_files(head_listing, read_head)
    problems = uncovered(collect(base_files, read_base), collect(head_files, read_head),
                         base_files, migrations.MIGRATIONS)
    for item in problems:
        wanted = "; ".join(f"{f}: {k}" for f, k in item["needs"])
        print(f"{item['file']}: the setting '{item['key']}' was removed or renamed.\n"
              f"    add a migration to labpipe/migrations.py MIGRATIONS (with a test) whose files "
              f"and old_keys cover: {wanted}\n"
              f"    (rename/move/remove/transform; method_setting() does all of a method's files)")
    changelog = ROOT / "CHANGELOG.md"
    notes = changelog_problems(migrations.MIGRATIONS, changelog.read_text(encoding="utf-8")
                               if changelog.exists() else None)
    for line in notes:
        print(line)
    if not problems and not notes:
        print(f"settings migrations: ok (compared with {base})")
    return 1 if problems or notes else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
