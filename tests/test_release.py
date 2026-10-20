"""Releases: the version, the changelog, tags, the CI check for announced changes, the release
script's changelog change, and the dependency updater (no network: latest versions are given)."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from labpipe import __version__, release

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"script_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_release = _script("check_release")
make_release = _script("release")
check_updates = _script("check_updates")

CHANGELOG = """# Changelog

## Unreleased

### Changes results

- `xrd`: background fit starts at 15° (why).

### New

- Something new,
  wrapped over two lines.

## v0.2.0 — 2026-11-01

### Settings migrated

- `plots.yaml`: renamed a key.

### Needs your action

None.

## v0.1.0 — 2026-10-16

### New

- First.
"""


# ---- version -------------------------------------------------------------------------------

def test_pyproject_version_is_the_package_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == __version__


def test_the_changelog_of_the_repository_parses_and_has_its_first_release():
    entries = release.parse_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert entries[0]["version"] == "Unreleased"
    assert release.parse_version(entries[1]["version"]) <= release.parse_version(__version__)


@pytest.mark.parametrize("text, expected", [("v0.2.0", (0, 2, 0)), ("0.10.3", (0, 10, 3))])
def test_parse_version(text, expected):
    assert release.parse_version(text) == expected


@pytest.mark.parametrize("text", ["0.2", "v1.2.3.4", "main", "v1.2.x", ""])
def test_parse_version_refuses_anything_else(text):
    with pytest.raises(ValueError):
        release.parse_version(text)


# ---- the changelog -------------------------------------------------------------------------

def test_parse_changelog_gives_entries_newest_first_with_all_four_parts():
    entries = release.parse_changelog(CHANGELOG)
    assert [e["version"] for e in entries] == ["Unreleased", "v0.2.0", "v0.1.0"]
    assert [e["date"] for e in entries] == [None, "2026-11-01", "2026-10-16"]
    assert list(entries[0]["sections"]) == list(release.PARTS)
    assert entries[0]["sections"]["Changes results"] == ["`xrd`: background fit starts at 15° (why)."]
    assert entries[0]["sections"]["New"] == ["Something new, wrapped over two lines."]
    assert entries[1]["sections"]["Needs your action"] == []           # "None."
    assert entries[2]["sections"]["Settings migrated"] == []


@pytest.mark.parametrize("old, new, message", [
    ("### New", "### News", "unknown part"),
    ("### Changes results\n", "### New\n\n- a\n\n### Changes results\n", "out of order"),
    ("## v0.2.0 — 2026-11-01", "## 0.2.0 (2026-11-01)", "bad heading"),
    ("## v0.2.0 — 2026-11-01", "## v0.0.5 — 2026-11-01", "newest first"),
    ("- `xrd`: background", "- background", "method name in backticks"),
    ("# Changelog\n", "", "# Changelog"),
    ("## Unreleased\n", "## v0.9.0 — 2026-12-01\n", "first entry"),
])
def test_parse_changelog_names_the_structural_problem(old, new, message):
    with pytest.raises(release.ReleaseError, match=message):
        release.parse_changelog(CHANGELOG.replace(old, new, 1))


def test_between_lists_what_is_newer_than_installed_up_to_the_target():
    entries = release.parse_changelog(CHANGELOG)
    assert [e["version"] for e in release.between(entries, "v0.1.0", "v0.2.0")] == ["v0.2.0"]
    assert [e["version"] for e in release.between(entries, "0.0.1", "0.2.0")] == ["v0.2.0", "v0.1.0"]
    assert release.between(entries, "v0.2.0", "v0.2.0") == []
    assert release.between(entries, "v0.1.0", "v0.1.0") == []


def test_between_for_a_downgrade_returns_what_would_be_undone_flagged():
    entries = release.parse_changelog(CHANGELOG)
    undone = release.between(entries, "v0.2.0", "v0.1.0")
    assert [(e["version"], e["undone"]) for e in undone] == [("v0.2.0", True)]


# ---- tags and current in a git checkout ----------------------------------------------------

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}


def _git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=folder, env={**os.environ, **GIT_ENV},
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    (folder / "a.txt").write_text("1\n", encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "one")
    return folder


def test_tags_are_sorted_by_version_and_only_releases(repo):
    for tag in ("v0.10.0", "v0.2.0", "v0.9.1", "nightly", "v1.0", "v0.2.0-rc1"):
        _git(repo, "tag", tag)
    assert release.tags(repo) == ["v0.2.0", "v0.9.1", "v0.10.0"]


def test_tags_of_a_folder_that_is_no_checkout_is_empty(tmp_path):
    assert release.tags(tmp_path) == []
    now = release.current(tmp_path)
    assert now == {"version": __version__, "tag": None, "commit": None, "dirty": False}


def test_current_says_tag_commit_and_local_changes(repo):
    now = release.current(repo)
    assert now["tag"] is None and re.fullmatch(r"[0-9a-f]{4,}", now["commit"]) and not now["dirty"]
    _git(repo, "tag", "v0.3.0")
    assert release.current(repo)["tag"] == "v0.3.0"
    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    assert release.current(repo)["dirty"] is True


# ---- scripts/check_release.py --------------------------------------------------------------

def _unreleased(**parts):
    sections = {p: [] for p in release.PARTS}
    sections.update({k.replace("_", " ").capitalize(): v for k, v in parts.items()})
    return sections


def test_an_unchanged_analysis_version_needs_nothing():
    assert check_release.check({"xrd": "a"}, {"xrd": "a"}, _unreleased(), {}) == ([], [])


def test_a_changed_analysis_version_needs_a_bullet_naming_the_method():
    problems, _ = check_release.check({"xrd": "a", "sem": "s"}, {"xrd": "b", "sem": "s"}, _unreleased(), {})
    assert len(problems) == 1 and "`xrd`" in problems[0] and "Changes results" in problems[0]
    assert "Results-unchanged: xrd" in problems[0]
    ok = _unreleased(changes_results=["`xrd`: background fit starts at 15° (why)."])
    assert check_release.check({"xrd": "a"}, {"xrd": "b"}, ok, {}) == ([], [])
    other = _unreleased(changes_results=["`eds`: something."])
    assert check_release.check({"xrd": "a"}, {"xrd": "b"}, other, {})[0]


def test_a_bullet_in_another_part_does_not_announce_changed_results():
    new_only = _unreleased(new=["`xrd`: a new thing."])
    assert check_release.check({"xrd": "a"}, {"xrd": "b"}, new_only, {})[0]


def test_a_new_method_needs_a_bullet_and_a_removed_one_a_changes_results_bullet():
    assert check_release.check({}, {"ftir": "x"}, _unreleased(), {})[0]
    assert check_release.check({}, {"ftir": "x"}, _unreleased(new=["`ftir`: new method."]), {}) == ([], [])
    assert check_release.check({"ftir": "x"}, {}, _unreleased(new=["`ftir`: x"]), {})[0]
    assert check_release.check({"ftir": "x"}, {}, _unreleased(changes_results=["`ftir`: removed."]), {}) == ([], [])


def test_results_unchanged_excuses_a_method_and_says_so():
    problems, bypassed = check_release.check({"xrd": "a"}, {"xrd": "b"}, _unreleased(),
                                             {"xrd": "refactor, numbers identical"})
    assert problems == [] and bypassed == ["`xrd`: refactor, numbers identical"]


def test_results_unchanged_trailers_are_read_from_commit_messages():
    log = ("Refactor fitting\n\nResults-unchanged: xrd moved a helper, same numbers\n"
           "Docs-checked: plots\n\nOther\n\nresults-unchanged: sem\n")
    assert check_release.trailers(log) == {"xrd": "moved a helper, same numbers",
                                           "sem": "(no reason given)"}


def test_analysis_versions_are_computed_in_a_checkout_and_change_with_the_method_code(tmp_path):
    """The real thing on a small copy: the versions of a code folder, before and after an edit."""
    code = tmp_path / "code"
    subprocess.run(["git", "-C", str(ROOT), "worktree", "list"], capture_output=True)   # git present
    import shutil
    shutil.copytree(ROOT, code, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.egg-info", ".pytest_cache", "analysis", "logs", "node_modules"))
    before = check_release.analysis_versions(code)
    assert {"xrd", "eds", "sem"} <= set(before)
    target = code / "src" / "labpipe" / "methods" / "sem" / "analyze.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    after = check_release.analysis_versions(code)
    assert after["sem"] != before["sem"] and after["xrd"] == before["xrd"]


# ---- scripts/release.py --------------------------------------------------------------------

def test_release_moves_unreleased_under_the_new_version():
    new, notes = make_release.release_changelog(CHANGELOG, "0.3.0", "2026-12-01")
    entries = release.parse_changelog(new)
    assert [e["version"] for e in entries] == ["Unreleased", "v0.3.0", "v0.2.0", "v0.1.0"]
    assert entries[1]["date"] == "2026-12-01"
    assert not any(entries[0]["sections"].values())                  # a new, empty Unreleased
    assert list(entries[0]["sections"]) == list(release.PARTS)
    assert entries[1]["sections"]["Changes results"] == ["`xrd`: background fit starts at 15° (why)."]
    assert notes.startswith("### Changes results") and "- `xrd`:" in notes
    assert "### Settings migrated" not in notes                      # empty parts are left out
    assert new.count("## v0.2.0") == 1                               # older entries untouched


def test_release_refuses_an_empty_unreleased_and_an_old_version():
    empty = CHANGELOG.replace("- `xrd`: background fit starts at 15° (why).", "None.").replace(
        "- Something new,\n  wrapped over two lines.", "None.")
    with pytest.raises(release.ReleaseError, match="no entries"):
        make_release.release_changelog(empty, "0.3.0", "2026-12-01")
    with pytest.raises(release.ReleaseError, match="not newer"):
        make_release.release_changelog(CHANGELOG, "0.2.0", "2026-12-01")


def test_release_bumps_both_version_lines():
    assert make_release.bump('x\n__version__ = "0.1.0"\n', r'^(__version__ = ")[^"]*(")', "0.2.0") \
        == 'x\n__version__ = "0.2.0"\n'
    assert 'version = "0.2.0"' in make_release.bump('[project]\nversion = "0.1.0"\n',
                                                    r'^(version = ")[^"]*(")', "0.2.0")


# ---- scripts/check_updates.py --------------------------------------------------------------

LOCK = "# comment numpy==9.9.9\nnumpy==2.5.4\nPyYAML==6.0.3\npython-dateutil==2.9.0.post0\n"


def _tree(tmp_path: Path, lock: str = LOCK, claude: str = "2.1.303") -> Path:
    (tmp_path / "container").mkdir()
    for name in check_updates.LOCKS:
        (tmp_path / name).write_text(lock, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        'dependencies = [\n    "PyYAML==6.0.3",\n]\nanalysis = ["numpy==2.5.4"]\n'
        'refbuild = ["pymatgen==2026.9.24"]\n', encoding="utf-8")
    (tmp_path / "container" / "versions.env").write_text(f"CLAUDE_CODE_VERSION={claude}\n", encoding="utf-8")
    return tmp_path


def test_pins_skip_comments_and_normalise_names():
    assert check_updates.pins(LOCK) == {"numpy": "2.5.4", "pyyaml": "6.0.3",
                                        "python-dateutil": "2.9.0.post0"}


def test_the_plan_lists_only_newer_versions_and_the_apply_rewrites_every_place(tmp_path):
    root = _tree(tmp_path)
    latest = {"numpy": "2.6.0", "pyyaml": "6.0.3", "pymatgen": "2026.10.1", "python-dateutil": "2.9.0.post0"}
    found = check_updates.plan(root, latest, "2.2.0")
    assert found["python"] == {"numpy": ("2.5.4", "2.6.0"), "pymatgen": ("2026.9.24", "2026.10.1")}
    assert found["claude"] == ("2.1.303", "2.2.0")
    assert check_updates.report(found)[0] == "numpy: 2.5.4 -> 2.6.0"
    check_updates.apply(root, found)
    for name in check_updates.LOCKS:
        text = (root / name).read_text(encoding="utf-8")
        assert "numpy==2.6.0" in text and "# comment numpy==9.9.9" in text and "PyYAML==6.0.3" in text
        assert "pymatgen" not in text                                 # never in a lock file
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"numpy==2.6.0"' in pyproject and '"pymatgen==2026.10.1"' in pyproject
    assert (root / "container" / "versions.env").read_text(encoding="utf-8") == "CLAUDE_CODE_VERSION=2.2.0\n"


def test_nothing_newer_means_nothing_to_do(tmp_path):
    root = _tree(tmp_path)
    found = check_updates.plan(root, {"numpy": "2.5.4"}, "2.1.303")
    assert found == {"python": {}, "claude": None} and check_updates.report(found) == []


def test_the_two_lock_files_must_stay_identical(tmp_path):
    root = _tree(tmp_path)
    (root / "requirements.lock").write_text(LOCK.replace("2.5.4", "2.5.3"), encoding="utf-8")
    with pytest.raises(ValueError, match="differ in: numpy"):
        check_updates.plan(root, {}, "2.1.303")


def test_the_real_lock_files_are_identical_and_without_pymatgen():
    root_pins = check_updates.pins((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    assert root_pins == check_updates.pins((ROOT / "container" / "requirements.lock").read_text(encoding="utf-8"))
    assert "pymatgen" not in root_pins


def test_prereleases_are_not_offered():
    assert check_updates.is_final("2.5.4") and check_updates.is_final("2.9.0.post0")
    assert not check_updates.is_final("3.0.0rc1") and not check_updates.is_final("3.0.0.dev1")
    assert check_updates.newer("2.9.0", "2.10.0") and not check_updates.newer("2.10.0", "2.9.0")
