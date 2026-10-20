"""`labpipe update` end to end, in a fake world made in a temporary folder (nothing of the user's is
touched, no network, no engine): a local bare "origin" with two releases made from copies of this
code (v0.2.0 as it is; v0.3.0 with a changelog entry, a synthetic settings migration and a small
change in the EDS calculation), an installation clone of it at v0.2.0 (native mode), and projects
made with that installation's `project new` with a little synthetic EDS and SEM data.

The "installer" of both releases is a stub that records how it was called: the real install.sh is
tested in test_install.py (it needs the network). Everything runs as subprocesses, the way users
run it, so the re-exec into the new code is the real one. The commands the container mode would
run are tested with a fake engine in test_update.py."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytest.importorskip("numpy")           # the analyses run natively here (the slim host job has none)


# The stub installer is a shell script, and the fake releases are commits of a Windows checkout
# whose line endings git converts, so the clone looks changed there. On Windows the update runs
# install.ps1, which is untested on a real Windows machine (README, docs/RELEASING.md).
pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="the fake world of this test is POSIX-only")

from tests.test_eds import make_example as make_eds                      # noqa: E402
from tests.test_sem import make_example as make_sem                      # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CODE_DIRS = ("src", "config", "references", "templates", "docs", "agents", "container", "hooks",
             ".claude", ".codex", ".agents")
CODE_FILES = (".gitignore", "methods.yaml", "pyproject.toml", "requirements.lock", "install.ps1", "AGENTS.md",
              "CLAUDE.md")
STUB_INSTALLER = """\
#!/bin/sh
# stands in for the release's own installer: records how it was called
dir="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$LABPIPE_HOME/installer-fails" ]; then echo broken >&2; exit 3; fi
{ echo "args: $*"; echo "channel: ${LABPIPE_CHANNEL:-}"; echo "no_setup: ${LABPIPE_NO_SETUP:-}"
  echo "switched: ${LABPIPE_SWITCHED:-}"; echo "full: ${LABPIPE_FULL:-}"
  sed -n 's/^__version__ = "\\(.*\\)"/version: \\1/p' "$dir/src/labpipe/__init__.py"; } \\
  > "$LABPIPE_HOME/reinstall.log"
"""
CHANGELOG = """\
# Changelog

What changed in each release, in four fixed parts.

## Unreleased

### Changes results

None.

### Settings migrated

None.

### New

None.

### Needs your action

None.
{entries}"""
V020 = """
## v0.2.0 — 2026-11-01

### New

- The first release of this test world.
"""
V030 = """
## v0.3.0 — 2026-12-01

### Changes results

- `eds`: the atomic weight of Pd is 106.40 (was 106.42), so at% of Pd changes slightly.

### Settings migrated

- `config/samples.yaml`: `properties.loading_wt` is now `properties.loading_weight`.

### New

- Something nice.

### Needs your action

None.
"""
MIGRATION = ('MIGRATIONS: list[Migration] = [Migration(\n'
             '    "0.3.0-loading", "v0.3.0", "properties.loading_wt is now properties.loading_weight",\n'
             '    ("config/samples.yaml",),\n'
             '    operations=(rename("samples.*.properties.loading_wt", '
             '"samples.*.properties.loading_weight"),))]')


def git(folder, *args, check=True):
    run = subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t",
                          *args], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0 or not check, run.stderr
    return run.stdout.strip()


def _copy_code(destination: Path) -> None:
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z", "--",
                             *CODE_DIRS, *CODE_FILES], cwd=REPO, capture_output=True, text=True,
                            encoding="utf-8", check=True).stdout.split("\0")
    for name in filter(None, listed):
        if (REPO / name).is_file():
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, destination / name)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _version(work: Path, version: str) -> None:
    current = re.search(r'__version__ = "(.*)"', (work / "src/labpipe/__init__.py").read_text(
        encoding="utf-8")).group(1)
    _replace(work / "src/labpipe/__init__.py", f'__version__ = "{current}"', f'__version__ = "{version}"')
    pyproject = (work / "pyproject.toml").read_text(encoding="utf-8")
    (work / "pyproject.toml").write_text(re.sub(r'(?m)^version = ".*"', f'version = "{version}"',
                                                pyproject, count=1), encoding="utf-8")


def _tree_digest(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


class World:
    """The origin with its two releases, and the raw data; each test builds its own installation."""

    def __init__(self, base: Path):
        self.base = base
        self.origin = base / "origin.git"
        self.work = base / "work"
        self.raw = base / "raw"
        self.work.mkdir()
        _copy_code(self.work)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        git(self.work, "init", "-q", "-b", "main")
        git(self.work, "remote", "add", "origin", str(self.origin))
        (self.work / "install.sh").write_text(STUB_INSTALLER, encoding="utf-8")
        _version(self.work, "0.2.0")
        (self.work / "CHANGELOG.md").write_text(CHANGELOG.format(entries=V020), encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "release 0.2.0")
        git(self.work, "tag", "v0.2.0")
        _version(self.work, "0.3.0")
        (self.work / "CHANGELOG.md").write_text(CHANGELOG.format(entries=V030 + V020),
                                                encoding="utf-8")
        _replace(self.work / "src/labpipe/migrations.py", "MIGRATIONS: list[Migration] = []", MIGRATION)
        _replace(self.work / "src/labpipe/methods/eds/analyze.py", '"Pd": 106.42', '"Pd": 106.40')
        git(self.work, "commit", "-q", "-am", "release 0.3.0")
        git(self.work, "tag", "v0.3.0")
        git(self.work, "push", "-q", "origin", "main", "--tags")
        make_eds(self.raw / "EDS", "S-9")
        make_eds(self.raw / "EDS", "S-10")
        make_sem(self.raw / "SEM", "S-10 a001")
        self.project = self._base_project()

    def _base_project(self) -> Path:
        """A project made and analysed with the v0.2.0 installation (copied for each test)."""
        install = Install(self, self.base / "install0")
        folder = self.base / "p0"
        run = install.labpipe("project", "new", str(folder), "--data", str(self.raw), cwd=self.base)
        assert run.returncode == 0, run.stdout + run.stderr
        with (folder / "config" / "local.yaml").open("a", encoding="utf-8") as stream:
            stream.write("automation: {settle_minutes: 0, notify: {desktop: false}}\n")
        (folder / "config" / "samples.yaml").write_text(
            "samples:\n  S-9:\n    properties:\n      loading_wt: 20   # wt%, our own name\n",
            encoding="utf-8")
        for args in (["scan"], ["analyze", "--pending"]):
            run = install.labpipe(*args, cwd=folder)
            assert run.returncode == 0, run.stdout + run.stderr
        entries = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["entries"]
        eds = sorted(e["entry_id"] for e in entries.values()
                     if e["method"] == "eds" and e["status"] == "done")
        assert len(eds) == 2 and sum(e["status"] == "done" for e in entries.values()) == 3
        entry = entries[eds[0]]
        results = json.loads((folder / entry["output_dir"] / "results.json").read_text(encoding="utf-8"))
        codes = " ".join(c["code"] for c in results["checks"] if c["severity"] == "review")
        (folder / entry["output_dir"] / "notes.md").write_text(
            f"# Notes\n\nresults.json sha256 {entry['results_sha256']}.\n\nCheck before using: {codes}\n",
            encoding="utf-8")
        return folder


class Install:
    """An installation (`$LABPIPE_HOME/app` at v0.2.0, native mode) in a folder of its own."""

    def __init__(self, world: World, folder: Path):
        self.world, self.folder = world, folder
        self.home = folder / "home"
        self.app = self.home / "app"
        self.home.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(world.origin), str(self.app)], check=True)
        git(self.app, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "v0.2.0")
        # an installation is older than the moment of its update (python takes a .pyc for current
        # when the source's size and whole-second mtime are the same: a test would be too quick)
        old = time.time() - 3600
        for path in self.app.rglob("*"):
            if ".git" not in path.relative_to(self.app).parts and path.is_file():
                os.utime(path, (old, old))
        (self.home / "install.json").write_text('{"mode": "full"}\n', encoding="utf-8")
        self.env = {**os.environ, "HOME": str(folder / "userhome"), "LABPIPE_HOME": str(self.home),
                    "LABPIPE_CONFIG_HOME": str(folder / "config"),
                    "PYTHONPATH": str(self.app / "src"), "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1"}
        for name in ("LABPIPE_PROJECT", "LABPIPE_ROOT", "LABPIPE_CODE", "LABPIPE_IN_CONTAINER",
                     "LABPIPE_ROOT_PATHS", "LABPIPE_BIN", "LABPIPE_VENV", "LABPIPE_FULL"):
            self.env.pop(name, None)

    def labpipe(self, *args, cwd, input=None):
        return subprocess.run([sys.executable, "-m", "labpipe.cli", *args], cwd=str(cwd), env=self.env,
                              capture_output=True, text=True, encoding="utf-8", input=input,
                              timeout=300)

    def describe(self) -> str:
        return git(self.app, "describe", "--tags", "--exact-match")

    def project(self, name: str = "project") -> Path:
        folder = self.folder / name
        shutil.copytree(self.world.project, folder)
        return folder


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return World(tmp_path_factory.mktemp("world"))


@pytest.fixture
def install(world, tmp_path) -> Install:
    return Install(world, tmp_path)


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _json(text: str):
    return json.loads(text)


# ---- what is available ------------------------------------------------------------------

def test_check_says_what_is_available_and_changes_nothing(install):
    project = install.project()
    before = _tree_digest(project)
    run = install.labpipe("update", "--check", cwd=project)
    assert run.returncode == 0, run.stderr
    summary = _json(run.stdout)
    assert (summary["installed"], summary["project"], summary["target"]) == ("v0.2.0", "v0.2.0", "v0.3.0")
    assert summary["direction"] == "update" and summary["up_to_date"] is False
    assert summary["changes_results"] == ["`eds`: the atomic weight of Pd is 106.40 (was 106.42), "
                                          "so at% of Pd changes slightly."]
    assert "--preview" in summary["next"]
    for text in ("v0.3.0 — 2026-12-01", "Changes results:", "Settings migrated:", "New:",
                 "Needs your action: none", "`eds`: the atomic weight of Pd"):
        assert text in run.stderr
    assert install.describe() == "v0.2.0" and _tree_digest(project) == before


def test_check_offline_uses_the_tags_that_are_here(install):
    project = install.project()
    git(install.app, "remote", "set-url", "origin", str(install.folder / "gone.git"))
    run = install.labpipe("update", "--check", cwd=project)
    assert run.returncode == 0 and _json(run.stdout)["target"] == "v0.3.0"
    assert "Could not reach the release server (offline?)" in run.stderr


def test_to_names_a_release_and_refuses_an_unknown_one(install):
    project = install.project()
    run = install.labpipe("update", "--check", "--to", "v0.9.0", cwd=project)
    assert run.returncode == 2 and "not a release of this installation" in _json(run.stdout)["error"]
    run = install.labpipe("update", "--check", "--to", "0.2.0", cwd=project)       # without the v
    assert "up to date" in run.stderr.lower() and _json(run.stdout)["up_to_date"] is True


def test_up_to_date_when_both_are_at_the_newest(install):
    project = install.project()
    git(install.app, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "v0.3.0")
    text = (project / "labpipe.yaml").read_text(encoding="utf-8")
    (project / "labpipe.yaml").write_text(text.replace("0.2.0", "0.3.0"), encoding="utf-8")
    run = install.labpipe("update", cwd=project)
    assert run.returncode == 0 and _json(run.stdout)["up_to_date"] is True
    assert "Up to date" in run.stderr


def test_dev_mode_refuses(install, world):
    run = subprocess.run([sys.executable, "-m", "labpipe.cli", "update", "--check"], cwd=str(world.work),
                         env={**install.env, "PYTHONPATH": str(world.work / "src")},
                         capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 2
    assert _json(run.stdout) == {"error": "dev mode: use git; releases are for installations"}


def test_a_clone_with_local_changes_is_not_switched(install):
    project = install.project()
    (install.app / "methods.yaml").write_text(
        (install.app / "methods.yaml").read_text(encoding="utf-8") + "# my edit\n", encoding="utf-8")
    before = _tree_digest(project)
    run = install.labpipe("update", "--yes", cwd=project)
    assert run.returncode == 2
    error = _json(run.stdout)["error"]
    assert "local changes" in error and "methods.yaml" in error
    assert install.describe() == "v0.2.0" and _tree_digest(project) == before   # nothing, not even a backup
    assert not (project / "backups").exists()


def test_without_yes_and_without_a_terminal_it_asks_for_one_and_changes_nothing(install):
    project = install.project()
    before = _tree_digest(project)
    run = install.labpipe("update", cwd=project)
    assert run.returncode == 2 and "--yes" in _json(run.stdout)["error"]
    assert install.describe() == "v0.2.0" and _tree_digest(project) == before


# ---- the update -------------------------------------------------------------------------

def test_the_update_switches_the_code_and_brings_the_project_along(install, world):
    project = install.project()
    raw_before = _tree_digest(world.raw)
    old_samples = (project / "config" / "samples.yaml").read_text(encoding="utf-8")
    run = install.labpipe("update", "--yes", cwd=project)
    assert run.returncode == 0, run.stdout + run.stderr
    summary = _json(run.stdout)
    err = run.stderr
    # the installation: this release's own installer, told to keep the checkout; the code is switched
    assert install.describe() == "v0.3.0"
    log = (install.home / "reinstall.log").read_text(encoding="utf-8")
    assert "args: --no-setup --keep-checkout --full" in log and "channel: keep" in log
    assert "version: 0.3.0" in log                       # it was the NEW installer, from the new checkout
    # before switching: the notes were checked with the old rules and the passes are on record
    entries = json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"]
    checked = [e for e in entries.values() if e.get("notes_check")]
    assert len(checked) == 1 and checked[0]["notes_check"]["labpipe"] == "0.2.0"
    assert summary["notes"]["checked"] == 1 and summary["notes"]["passed"] == 1
    assert err.index("Checking the notes") < err.index("Backed up") < err.index("Switching")
    # the backup
    backup = project / "backups" / "v0.2.0"
    assert summary["backup"] == "backups/v0.2.0"
    assert (backup / "labpipe.yaml").is_file() and (backup / "manifest.json").is_file()
    assert (backup / "config" / "samples.yaml").read_text(encoding="utf-8") == old_samples
    # the migration: the diff was shown, the file changed, comments kept, the original is kept
    assert "-      loading_wt: 20" in err and "+      loading_weight: 20" in err
    assert "properties.loading_wt is now properties.loading_weight" in err
    new_samples = (project / "config" / "samples.yaml").read_text(encoding="utf-8")
    assert new_samples == old_samples.replace("loading_wt", "loading_weight")      # comment kept
    assert summary["settings_migrated"] == ["config/samples.yaml"]
    assert (backup / "migrated" / "config" / "samples.yaml").read_text(encoding="utf-8") == old_samples
    # sync, the record
    generated = json.loads((project / ".labpipe" / "generated.json").read_text(encoding="utf-8"))
    assert generated["labpipe_version"] == "0.3.0"
    record = _yaml(project / "labpipe.yaml")
    assert record["labpipe_version"] == "0.3.0"
    assert [{k: v for k, v in u.items() if k != "utc"} for u in record["updates"]] == [
        {"from": "0.2.0", "to": "0.3.0", "backup": "backups/v0.2.0", "preview": False}]
    # what is out of date now (only eds: its calculation changed), nothing reanalysed
    assert summary["stale"] == {"eds": 2}
    assert "eds: 2 analyses" in err and "labpipe requeue --stale" in err and "labpipe analyze --pending" in err
    assert "tell Claude Code: redo the out-of-date analyses" in err
    after = json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"]
    assert sorted(e["status"] for e in after.values() if e["method"] == "eds").count("stale") == 2
    assert "stale" not in {e["status"] for e in after.values() if e["method"] == "sem"}
    # the state, the doctor summary, raw data
    state = json.loads((project / summary["state"]).read_text(encoding="utf-8"))
    assert state["step"] == "doctor" and state["from"] == "v0.2.0" and state["to"] == "v0.3.0"
    assert "labpipe doctor:" in err and summary["doctor"] is not None
    assert "project_release" not in {c["check"] for c in summary["doctor"]["attention"]}
    assert _tree_digest(world.raw) == raw_before
    # a second run: up to date
    again = install.labpipe("update", cwd=project)
    assert again.returncode == 0 and _json(again.stdout)["up_to_date"] is True


def test_a_rollback_goes_back_and_restores_the_settings_from_the_backup(install):
    project = install.project()
    assert install.labpipe("update", "--yes", cwd=project).returncode == 0
    assert "loading_weight" in (project / "config" / "samples.yaml").read_text(encoding="utf-8")
    run = install.labpipe("update", "--to", "v0.2.0", "--yes", cwd=project)
    assert run.returncode == 0, run.stdout + run.stderr
    summary, err = _json(run.stdout), run.stderr
    assert summary["direction"] == "rollback" and install.describe() == "v0.2.0"
    assert "Going back to v0.2.0" in err and "(undone by going back)" in err
    assert "Go back to v0.2.0? [y/N] y (--yes)" in err
    assert "v0.3.0 — 2026-12-01" in err                        # what will be undone
    # the settings: the diff against the backup made when the project left v0.2.0, then restored
    assert "differs from your settings in 1 file(s)" in err and "+      loading_wt: 20" in err
    assert summary["settings_restored"] == ["config/samples.yaml"]
    assert "loading_wt: 20" in (project / "config" / "samples.yaml").read_text(encoding="utf-8")
    assert "loading_weight" in (project / "backups" / "v0.3.0" / "config" / "samples.yaml").read_text(
        encoding="utf-8")                                   # what it was before: kept
    record = _yaml(project / "labpipe.yaml")
    assert record["labpipe_version"] == "0.2.0" and [(u["from"], u["to"]) for u in record["updates"]] == [
        ("0.2.0", "0.3.0"), ("0.3.0", "0.2.0")]
    assert "version: 0.2.0" in (install.home / "reinstall.log").read_text(encoding="utf-8")
    assert "Looking for analyses that are out of date" in err
    # the eds analyses were made with v0.2.0's calculation: done again, not out of date
    assert summary["stale"] == {}, summary["stale"]


def test_a_rollback_without_a_backup_says_so(install):
    project = install.project()
    assert install.labpipe("update", "--yes", cwd=project).returncode == 0
    shutil.rmtree(project / "backups" / "v0.2.0")
    run = install.labpipe("update", "--to", "v0.2.0", "--yes", cwd=project)
    assert run.returncode == 0 and "no backup of your settings from when this project left v0.2.0" in run.stderr.replace(
        "\n", " ") or "There is no backup of your settings" in run.stderr
    assert "loading_weight" in (project / "config" / "samples.yaml").read_text(encoding="utf-8")


def test_a_second_project_only_does_the_project_steps(install):
    first, second = install.project("one"), install.project("two")
    assert install.labpipe("update", "--yes", cwd=first).returncode == 0
    (install.home / "reinstall.log").unlink()
    # the installation is at v0.3.0 now; the second project still says v0.2.0
    doctor = _json(install.labpipe("doctor", cwd=second).stdout)
    item = next(c for c in doctor["checks"] if c["check"] == "project_release")
    assert item["status"] == "warn" and "set up with labpipe 0.2.0; the installation is 0.3.0" in item["message"]
    run = install.labpipe("update", "--yes", cwd=second)
    assert run.returncode == 0, run.stdout + run.stderr
    err = run.stderr
    assert "already at v0.3.0 (another project updated it)" in err
    assert "Switching" not in err and "Reinstalling" not in err
    assert not (install.home / "reinstall.log").exists()                # no installer, no checkout
    assert install.describe() == "v0.3.0"
    assert (second / "backups" / "v0.2.0" / "config" / "samples.yaml").is_file()
    assert "loading_weight" in (second / "config" / "samples.yaml").read_text(encoding="utf-8")
    assert _yaml(second / "labpipe.yaml")["labpipe_version"] == "0.3.0"
    assert _json(run.stdout)["stale"] == {"eds": 2}
    doctor = _json(install.labpipe("doctor", cwd=second).stdout)
    assert "project_release" not in {c["check"] for c in doctor["checks"]}


def test_a_failing_installer_names_the_way_on_and_the_update_resumes(install):
    project = install.project()
    (install.home / "installer-fails").write_text("", encoding="utf-8")
    run = install.labpipe("update", "--yes", cwd=project)
    assert run.returncode == 2 and "the installer failed" in _json(run.stdout)["error"]
    assert "broken" in run.stderr and install.describe() == "v0.3.0"     # the code was switched
    state = re.search(r"labpipe update --resume (\S+)", _json(run.stdout)["error"]).group(1)
    assert _yaml(project / "labpipe.yaml")["labpipe_version"] == "0.2.0"  # the project is not recorded yet
    (install.home / "installer-fails").unlink()                          # fixed: the update goes on
    resumed = install.labpipe("update", "--resume", state, cwd=project)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert _yaml(project / "labpipe.yaml")["labpipe_version"] == "0.3.0"
    assert len(list((project / "backups").iterdir())) == 1                # one backup, not two


# ---- the preview ------------------------------------------------------------------------

def test_the_preview_says_what_changes_and_leaves_the_project_untouched(install, world):
    project = install.project()
    before, raw_before = _tree_digest(project), _tree_digest(world.raw)
    run = install.labpipe("update", "--check", "--preview", cwd=project)
    assert run.returncode == 0, run.stdout + run.stderr
    summary = _json(run.stdout)
    lines = summary["preview"]["summary"]
    assert any(line.startswith("eds: 2 measurements; at.Pd changes up to ") and "(no σ given)" in line
               for line in lines), lines
    assert "sem: unchanged (the calculation is the same)." in lines
    scratch = install.home / "preview" / "project-v0.3.0"
    preview = json.loads((scratch / "preview.json").read_text(encoding="utf-8"))
    assert summary["preview"]["file"] == (scratch / "preview.json").as_posix()
    assert preview["methods"]["sem"]["status"] == "unchanged"
    pd = preview["methods"]["eds"]["quantities"]["eds.at.Pd"]
    assert pd["n"] == 2 and 0 < pd["max_abs_change"] < 0.1 and pd["within_sigma"] is None
    assert preview["methods"]["eds"]["analysis_version"]["from"] != preview["methods"]["eds"]["analysis_version"]["to"]
    assert preview["mode"] == "native" and preview["problems"] == []
    # nothing in the project, nothing in the installation, nothing in the data
    assert _tree_digest(project) == before and _tree_digest(world.raw) == raw_before
    assert install.describe() == "v0.2.0" and not git(install.app, "status", "--porcelain")
    # the target's code is a cache; the scratch project has the analyses of the changed method only
    assert (install.home / "releases" / "v0.3.0" / "src" / "labpipe" / "update.py").is_file()
    assert not list((scratch / "analysis").glob("*/sem")) and list((scratch / "analysis").glob("*/eds"))
    assert not (scratch / "context").exists()
    # again, with a limit: reuses the release folder
    limited = install.labpipe("update", "--check", "--preview", "--limit", "1", cwd=project)
    assert limited.returncode == 0, limited.stderr
    assert any("eds: 1 measurement (the first 1 of 2);" in line for line in _json(limited.stdout)["preview"]["summary"])
    assert _tree_digest(project) == before


def test_the_preview_before_the_update_is_recorded(install):
    project = install.project()
    run = install.labpipe("update", "--preview", "--yes", cwd=project)
    assert run.returncode == 0, run.stdout + run.stderr
    assert _yaml(project / "labpipe.yaml")["updates"][0]["preview"] is True
    assert "Preview of v0.3.0" in run.stderr and "eds: 2 measurements" in run.stderr
