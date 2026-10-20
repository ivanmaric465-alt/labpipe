"""Code and projects: two roots, `project new | sync`, overlays, the generated files, dev mode."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from labpipe import agents, doctor, guard, project, safety, settings
from tests.conftest import fcc_positions, synthetic_pattern, xrdml_text

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """No user defaults file; nothing of the real home is read."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in ("LABPIPE_PROJECT", "LABPIPE_ROOT", "LABPIPE_CODE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _new(tmp_path, name="proj", **kwargs):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return project.new(tmp_path / "work" / name, kwargs.pop("data", [str(data)]), **kwargs)


def _in_project(monkeypatch, folder: Path) -> Path:
    """Make `folder` the current project for this process (as if run inside it)."""
    folder = folder.resolve()
    for module in (safety, project, agents, guard, doctor):
        monkeypatch.setattr(module, "PROJECT_ROOT", folder)
    monkeypatch.setattr(agents, "HARNESSES", {k: folder / v for k, v in agents.HARNESS_DIRS.items()})
    monkeypatch.setattr(safety, "NO_PROJECT", False)
    return folder


# ---- finding the project ---------------------------------------------------------------

def test_the_project_is_found_from_a_subfolder(tmp_path):
    root = tmp_path / "p"
    (root / "analysis" / "S-1" / "xrd").mkdir(parents=True)
    (root / "labpipe.yaml").write_text("schema: labpipe.project/1\n", encoding="utf-8")
    assert safety.find_project(root / "analysis" / "S-1" / "xrd") == root.resolve()
    assert safety.find_project(root) == root.resolve()
    assert safety.find_project(tmp_path) is None
    inner = root / "analysis" / "inner"
    inner.mkdir()
    (inner / "labpipe.yaml").write_text("x: 1\n", encoding="utf-8")
    assert safety.find_project(inner / "..") == root.resolve()
    assert safety.find_project(inner) == inner.resolve()      # the nearest one


def test_roots_from_environment_folder_and_dev_mode(tmp_path, monkeypatch):
    root = tmp_path / "p"
    (root / "sub").mkdir(parents=True)
    (root / "labpipe.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.chdir(root / "sub")
    code, found, none = safety._roots()
    assert found == root.resolve() and not none and code == REPO
    monkeypatch.setenv("LABPIPE_ROOT", str(tmp_path / "alias"))            # the old name still works
    assert safety._roots()[1] == tmp_path / "alias"
    monkeypatch.setenv("LABPIPE_PROJECT", str(tmp_path / "chosen"))        # and the new one wins
    assert safety._roots()[1] == tmp_path / "chosen"
    monkeypatch.setenv("LABPIPE_CODE", str(tmp_path / "code"))
    assert safety._roots()[0] == tmp_path / "code"
    monkeypatch.delenv("LABPIPE_PROJECT")
    monkeypatch.delenv("LABPIPE_ROOT")
    monkeypatch.delenv("LABPIPE_CODE")
    monkeypatch.chdir(tmp_path)                                            # no project around
    assert safety._roots() == (REPO, REPO, False)                          # dev mode: its own project


def test_an_installation_is_not_a_project(tmp_path, monkeypatch):
    code = tmp_path / "app"
    code.mkdir()
    (code / ".labpipe-installed").write_text("", encoding="utf-8")
    monkeypatch.setenv("LABPIPE_CODE", str(code))
    monkeypatch.chdir(tmp_path)
    assert safety._roots() == (code, code, True)


def _code_copy(tmp_path) -> Path:
    """A copy of the code (no git, no tests): a stand-in for an installation or a repository."""
    code = tmp_path / "code"
    ignore = shutil.ignore_patterns(".git", "__pycache__", "tests", ".pytest_cache", "analysis",
                                    "logs", "context", "*.egg-info")
    for item in ("src", "config", "agents", "docs", "references", "templates", "hooks",
                 ".claude", ".codex", ".agents", "container", "scripts"):
        shutil.copytree(REPO / item, code / item, ignore=ignore)
    for item in ("AGENTS.md", "CLAUDE.md", "methods.yaml", "pyproject.toml", "requirements.lock"):
        shutil.copy(REPO / item, code / item)
    for lab in ("local", "lab", "samples", "sample_ids", "ignore"):
        (code / "config" / f"{lab}.yaml").unlink(missing_ok=True)
    shutil.rmtree(code / ".claude" / "agents", ignore_errors=True)   # generated, git-ignored
    return code


def _repo_state() -> dict:
    """The repository's own project files (a developer's checkout may already have some)."""
    files = [REPO / "manifest.json", *(REPO / "analysis").rglob("*")]
    return {p: p.stat().st_mtime_ns for p in files if p.is_file()}


def _run(args, cwd, **env):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("LABPIPE_")}
    environment.update(PYTHONPATH=str(REPO / "src"), **env)
    done = subprocess.run([sys.executable, "-m", "labpipe.cli", *args], cwd=cwd, env=environment,
                          capture_output=True, text=True, encoding="utf-8", timeout=600)
    return done.returncode, json.loads(done.stdout)


def test_commands_outside_a_project_explain_how_to_make_one(tmp_path):
    code = _code_copy(tmp_path)
    (code / ".labpipe-installed").write_text("", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code_env = {"LABPIPE_CODE": str(code), "HOME": str(tmp_path / "home")}
    status, out = _run(["scan"], elsewhere, **code_env)
    assert status == 2
    assert "not in a labpipe project; create one with `labpipe project new <folder>`" in out["error"]
    status, out = _run(["doctor"], elsewhere, **code_env)
    assert status == 1 and out["ready"] is False and out["next"]["do"].startswith("labpipe project new")
    status, out = _run(["project", "new", str(tmp_path / "made")], elsewhere, **code_env)
    assert status == 0 and (tmp_path / "made" / "labpipe.yaml").exists()


# ---- project new -----------------------------------------------------------------------

def test_project_new_creates_the_structure(tmp_path):
    result = _new(tmp_path, name="pd-films")
    folder = Path(result["project"])
    for path in ("labpipe.yaml", "config/local.yaml", "config/lab.yaml", "config/samples.yaml",
                 "lab/LAB.md", "lab/agents/README.md", "AGENTS.md", "CLAUDE.md",
                 ".labpipe/docs/ARCHITECTURE.md", ".labpipe/generated.json",
                 ".claude/settings.json", ".claude/agents/supervisor.md",
                 ".codex/hooks.json", ".labpipe/hooks/guard.py"):
        assert (folder / path).exists(), path
    meta = yaml.safe_load((folder / "labpipe.yaml").read_text(encoding="utf-8"))
    assert meta["schema"] == "labpipe.project/1" and meta["name"] == "pd-films"
    assert meta["created_utc"].endswith("Z") and meta["labpipe_version"]
    local = yaml.safe_load((folder / "config" / "local.yaml").read_text(encoding="utf-8"))
    assert local["raw_roots"] == [{"id": "data", "path": (tmp_path / "data").as_posix()}]
    assert local["protected_paths"] == [(tmp_path / "data").as_posix()]   # doctor: not a todo
    assert local["agents"]["harness"] == "claude-code"          # from the example
    assert "labpipe project sync" in (folder / "lab" / "LAB.md").read_text(encoding="utf-8")
    assert result["next"][0] == f"cd {folder}" and "labpipe doctor" in result["next"]
    assert not list((tmp_path / "data").iterdir())              # the data folder is not touched


def test_project_new_takes_the_agents_from_the_users_defaults(tmp_path, _home):
    (_home / ".config" / "labpipe").mkdir(parents=True)
    (_home / ".config" / "labpipe" / "defaults.yaml").write_text(
        "agents: {harness: codex, models: {supervisor: openai/x}}\n", encoding="utf-8")
    first, second = tmp_path / "d1", tmp_path / "d2"
    first.mkdir(), second.mkdir()
    result = project.new(tmp_path / "work" / "p", [str(first), str(second), str(tmp_path / "d1")])
    local = yaml.safe_load((Path(result["project"]) / "config" / "local.yaml").read_text(encoding="utf-8"))
    assert local["agents"] == {"harness": "codex", "models": {"supervisor": "openai/x"}}
    assert [r["id"] for r in local["raw_roots"]] == ["d1", "d2", "d1-2"]
    assert (Path(result["project"]) / ".codex" / "agents" / "supervisor.toml").exists()



def test_doctor_asks_the_container_for_the_files_when_this_host_only_forwards(tmp_path,
                                                                              monkeypatch):
    from labpipe import container
    config = {"raw_roots": [{"id": "data", "path": str(tmp_path)}]}
    monkeypatch.setattr(doctor, "_host_only", lambda: True)
    asked = []
    listed = {"roots": [{"id": "data", "data_folder": None, "reads_from": "/data/data",
                         "recognised": {"eds": {"files": {".htm (analysed)": 2}}},
                         "not_recognised": {},
                         "no_sample_id": {"files": 0, "pattern": "p", "examples": []}}]}
    monkeypatch.setattr(container, "forward_json", lambda argv: asked.append(argv) or listed)
    items = doctor._files(config, [("data", tmp_path)])
    assert asked == [["inventory"]]          # the method code needs the analysis libraries
    assert [i["check"] for i in items] == ["data_folder:data", "files:data", "sample_ids:data"]
    assert items[1]["status"] == "ok"

    def stopped(argv):
        raise container.ContainerError("container labpipe-p is not running")
    monkeypatch.setattr(container, "forward_json", stopped)
    [item] = doctor._files(config, [("data", tmp_path)])
    assert item["check"] == "files" and item["status"] == "warn" and "not running" in item["message"]

def test_project_new_refuses_a_synced_folder(tmp_path):
    for name in ("Dropbox", "OneDrive - Lab", "Google Drive", "Nextcloud", "Koofr", "iCloud Drive",
                 "Seafile", "Syncthing", "MEGA", "ownCloud"):
        with pytest.raises(project.ProjectError, match="synced folder"):
            project.new(tmp_path / name / "proj")
        assert not (tmp_path / name).exists()
    assert safety.synced_marker(tmp_path / "labpipe-projects" / "a") is None


def test_project_new_refuses_a_non_empty_folder_that_is_not_a_project(tmp_path):
    folder = tmp_path / "full"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(project.ProjectError, match="not empty"):
        project.new(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]
    (tmp_path / "file").write_text("x", encoding="utf-8")
    with pytest.raises(project.ProjectError):
        project.new(tmp_path / "file")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (Path(project.new(empty)["project"]) / "labpipe.yaml").exists()      # empty is fine


def test_project_new_refuses_nesting_and_missing_data(tmp_path):
    made = Path(_new(tmp_path)["project"])
    with pytest.raises(project.ProjectError, match="do not nest"):
        project.new(made / "inner")
    with pytest.raises(project.ProjectError, match="data folder not found"):
        project.new(tmp_path / "other", [str(tmp_path / "nowhere")])
    data = tmp_path / "bigdata"
    data.mkdir()
    with pytest.raises(project.ProjectError, match="must not be inside the data"):
        project.new(data / "proj", [str(data)])


def test_project_new_on_a_project_never_overwrites_the_users_files(tmp_path):
    folder = Path(_new(tmp_path)["project"])
    (folder / "lab" / "LAB.md").write_text("our rules\n", encoding="utf-8")
    (folder / "config" / "samples.yaml").write_text("samples: {S-1: {}}\n", encoding="utf-8")
    again = project.new(folder)
    assert "lab/LAB.md" in again["kept"] and again["created"] == []
    assert (folder / "lab" / "LAB.md").read_text(encoding="utf-8") == "our rules\n"
    assert (folder / "config" / "samples.yaml").read_text(encoding="utf-8") == "samples: {S-1: {}}\n"
    assert "our rules" in (folder / "AGENTS.md").read_text(encoding="utf-8")      # and the sync picked it up


# ---- two roots in the settings ---------------------------------------------------------

def test_defaults_come_from_the_code_and_lab_files_from_the_project(tmp_path, monkeypatch):
    folder = _in_project(monkeypatch, Path(_new(tmp_path)["project"]))
    monkeypatch.setattr(settings, "CONFIG", folder / "config")
    (folder / "config" / "lab.yaml").write_text("xrd: {background: {order: 5}}\n"
                                                "plots: {font_size: 8}\n", encoding="utf-8")
    (folder / "config" / "samples.yaml").write_text("samples: {S-1: {xrd: {phases: [Pd]}}}\n", encoding="utf-8")
    (folder / "config" / "measurements" / "xrd").mkdir(parents=True)
    (folder / "config" / "measurements" / "xrd" / "S-1_a.yaml").write_text(
        "note: x\nbackground: {order: 2}\n", encoding="utf-8")
    assert not (folder / "config" / "xrd.yaml").exists()         # no copy of the defaults
    resolved, sources = settings.resolve("xrd", "S-1", "S-1_a")
    assert resolved["background"]["order"] == 2 and resolved["phases"] == ["Pd"]
    by_layer = {s["layer"].split()[0]: s for s in sources}
    assert by_layer["global"] == {"file": "config/xrd.yaml", "root": "code",
                                  "sha256": hashlib.sha256((REPO / "config" / "xrd.yaml")
                                                           .read_bytes()).hexdigest(),
                                  "layer": "global defaults"}
    assert by_layer["plot"]["root"] == "code" and by_layer["plot"]["file"] == "config/plots.yaml"
    for layer, file in (("lab", "config/lab.yaml"), ("sample", "config/samples.yaml"),
                        ("measurement", "config/measurements/xrd/S-1_a.yaml")):
        assert by_layer[layer]["root"] == "project" and by_layer[layer]["file"] == file
        assert by_layer[layer]["sha256"] == hashlib.sha256((folder / file).read_bytes()).hexdigest()


# ---- the generated files ---------------------------------------------------------------

def test_generated_agents_md_has_the_lab_additions_and_readable_doc_paths(tmp_path):
    folder = Path(_new(tmp_path)["project"])
    (folder / "lab" / "LAB.md").write_text("We call the support Vulcan.\n", encoding="utf-8")
    project.sync(folder)
    text = (folder / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("<!-- Generated by labpipe ") and "Do not edit; your additions go in " \
        "lab/LAB.md (or lab/agents/)" in text.splitlines()[0]
    assert text.rstrip().endswith("We call the support Vulcan.")
    assert "\n## This lab's additions (lab/LAB.md)\n" in text
    source = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count("`.labpipe/docs/SAFETY.md`") == source.count("`docs/SAFETY.md`") > 0
    assert "`docs/" not in text and "`.labpipe/docs/methods/<method>.md`" in text
    assert "`.labpipe/config/<method>.yaml`" in text and "`.labpipe/methods.yaml`" in text
    assert "`config/samples.yaml`" in text and "`config/local.yaml`" in text    # the lab's own
    claude = (folder / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith("<!-- Generated by labpipe ") and "@AGENTS.md" in claude
    assert claude.rstrip().endswith("We call the support Vulcan.") and "`docs/" not in claude
    for doc in (REPO / "docs").rglob("*.md"):                    # the docs are copied as they are
        copy = folder / ".labpipe" / "docs" / doc.relative_to(REPO / "docs")
        assert copy.read_bytes() == doc.read_bytes()
    assert (folder / ".labpipe" / "config" / "xrd.yaml").read_bytes() == (
        REPO / "config" / "xrd.yaml").read_bytes()
    assert not (folder / ".labpipe" / "config" / "local.yaml").exists()
    assert (folder / ".labpipe" / "methods.yaml").exists()
    skill = (folder / ".agents" / "skills" / "labpipe-pass" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---") and "`.labpipe/agents/supervisor.md`" in skill


def test_generated_harness_settings_are_adapted_to_the_project(tmp_path):
    folder = Path(_new(tmp_path)["project"])
    settings_json = json.loads((folder / ".claude" / "settings.json").read_text(encoding="utf-8"))
    command = settings_json["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == 'python3 "${CLAUDE_PROJECT_DIR}/.labpipe/hooks/guard.py"'
    assert "Edit(/.labpipe/**)" in settings_json["permissions"]["deny"]
    assert "Edit(/AGENTS.md)" in settings_json["permissions"]["deny"]
    assert "Edit(/lab/**)" in settings_json["permissions"]["ask"]
    assert settings_json["permissions"]["allow"] == json.loads(
        (REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["allow"]
    for name in ("unattended.json", "unattended-container.json"):
        deny = json.loads((folder / ".claude" / name).read_text(encoding="utf-8"))["permissions"]["deny"]
        assert "Edit(**/.labpipe/**)" in deny and "Edit(**/AGENTS.md)" in deny
    hook = json.loads((folder / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "labpipe.yaml" in hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert ".labpipe/hooks/guard.py" in hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert (folder / ".codex" / "rules" / "labpipe.rules").read_text(encoding="utf-8").startswith(
        "# Generated by labpipe ")
    assert (folder / ".codex" / "config.toml").read_text(encoding="utf-8").startswith("# Generated by labpipe ")
    # the repository's own files are what developers use: unchanged by all this
    assert "/hooks/guard.py" in (REPO / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert ".labpipe" not in (REPO / ".claude" / "settings.json").read_text(encoding="utf-8")


def test_the_launcher_finds_its_project(tmp_path):
    folder = Path(_new(tmp_path)["project"])
    launcher = folder / ".labpipe" / "hooks" / "guard.py"
    event = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(folder / "AGENTS.md")},
                        "cwd": str(tmp_path)})
    env = {k: v for k, v in os.environ.items() if not k.startswith("LABPIPE_")}
    env.update(PYTHONPATH=str(REPO / "src"), LABPIPE_PYTHON=sys.executable)
    done = subprocess.run([sys.executable, str(launcher)], input=event, env=env,
                          capture_output=True, text=True, cwd=tmp_path)
    assert done.returncode == 2 and "generated by labpipe" in done.stderr


def test_sync_is_idempotent_and_follows_the_lab_file(tmp_path):
    folder = Path(_new(tmp_path)["project"])
    first = project.sync(folder)
    assert first["written"] == [] and first["unchanged"] > 20       # nothing changed since `new`
    record = (folder / ".labpipe" / "generated.json").read_text(encoding="utf-8")
    (folder / "lab" / "LAB.md").write_text("new rule\n", encoding="utf-8")
    second = project.sync(folder)
    assert second["written"] == ["AGENTS.md", "CLAUDE.md"]
    assert (folder / ".labpipe" / "generated.json").read_text(encoding="utf-8") != record
    assert project.sync(folder)["written"] == []


def test_sync_removes_only_files_it_generated_before(tmp_path):
    folder = Path(_new(tmp_path)["project"])
    (folder / ".labpipe" / "docs" / "OLD.md").write_text("x", encoding="utf-8")           # not generated by labpipe
    recorded = json.loads((folder / ".labpipe" / "generated.json").read_text(encoding="utf-8"))
    recorded["files"][".labpipe/docs/GONE.md"] = "0" * 64              # generated once, gone since
    (folder / ".labpipe" / "docs" / "GONE.md").write_text("old", encoding="utf-8")
    (folder / ".labpipe" / "generated.json").write_text(json.dumps(recorded), encoding="utf-8")
    result = project.sync(folder)
    assert result["removed"] == [".labpipe/docs/GONE.md"]
    assert (folder / ".labpipe" / "docs" / "OLD.md").exists()
    assert not (folder / ".labpipe" / "docs" / "GONE.md").exists()


def test_doctor_says_when_generated_files_are_out_of_date(tmp_path, monkeypatch):
    folder = _in_project(monkeypatch, Path(_new(tmp_path)["project"]))
    [item] = doctor._project()
    assert item["status"] == "ok" and "generated files current" in item["message"]
    assert project.status()["mode"] == "project"
    (folder / "lab" / "LAB.md").write_text("changed\n", encoding="utf-8")
    [item] = doctor._project()
    assert item["status"] == "todo" and item["next"] == "run `labpipe project sync`"
    project.sync(folder)
    assert doctor._project()[0]["status"] == "ok"
    (folder / "AGENTS.md").write_text("edited by hand\n", encoding="utf-8")
    [item] = doctor._project()
    assert item["status"] == "todo" and "edited by hand" in item["message"]
    (folder / "AGENTS.md").unlink()
    assert "missing: AGENTS.md" in doctor._project()[0]["message"]


# ---- the lab's overlays ----------------------------------------------------------------

def test_overlays_are_appended_and_extra_roles_added(tmp_path):
    folder = Path(_new(tmp_path)["project"])
    (folder / "lab" / "agents" / "xrd-analyst.md").write_text("Call the support Vulcan.\n", encoding="utf-8")
    (folder / "lab" / "agents" / "tem-reader.md").write_text(
        "---\nname: tem-reader\nrole: analyst\ndescription: Reads TEM notes.\n"
        "capabilities: [shell, read]\n---\nRead the TEM description.\n", encoding="utf-8")
    result = project.sync(folder)
    assert result["problems"] == []
    analyst = (folder / ".claude" / "agents" / "xrd-analyst.md").read_text(encoding="utf-8")
    source = (REPO / "agents" / "xrd-analyst.md").read_text(encoding="utf-8").split("---", 2)[2].strip()
    body = analyst.split("-->", 1)[1]
    assert source.splitlines()[0] in body and "`docs/methods/xrd.md`" not in body
    assert "`.labpipe/docs/methods/xrd.md`" in body
    assert body.rstrip().endswith("## This lab's additions (lab/agents/xrd-analyst.md)\n\n"
                                  "Call the support Vulcan.")
    assert "from .labpipe/agents/xrd-analyst.md + lab/agents/xrd-analyst.md" in analyst
    extra = (folder / ".claude" / "agents" / "tem-reader.md").read_text(encoding="utf-8")
    assert "name: tem-reader" in extra and "Read the TEM description." in extra
    assert (folder / ".claude" / "agents" / "tem-reader.md").as_posix().endswith("tem-reader.md")
    assert ".claude/agents/tem-reader.md" in json.loads(
        (folder / ".labpipe" / "generated.json").read_text(encoding="utf-8"))["files"]
    # the README next to them is not an overlay
    assert "README" not in " ".join(p.name for p in (folder / ".claude" / "agents").iterdir())


def test_an_overlay_for_a_role_that_does_not_exist_is_reported(tmp_path, monkeypatch):
    folder = _in_project(monkeypatch, Path(_new(tmp_path)["project"]))
    (folder / "lab" / "agents" / "xrd-analist.md").write_text("typo in the name\n", encoding="utf-8")
    (folder / "lab" / "agents" / "half.md").write_text("---\nname: half\n---\nbody\n", encoding="utf-8")
    result = project.sync(folder)
    assert len(result["problems"]) == 2
    assert any("xrd-analist" in p and "does not exist" in p and "xrd-analyst" in p
               for p in result["problems"])
    assert any("half.md" in p and "needs name, role, description" in p for p in result["problems"])
    assert not (folder / ".claude" / "agents" / "xrd-analist.md").exists()     # not turned into a role
    assert agents.status(safety.load_pipeline_config())["problems"] == result["problems"]
    items = [i for i in doctor.run()["checks"] if i["check"] == "lab_agents"]
    assert len(items) == 2 and items[0]["status"] == "warn"
    status, out = _run(["agents", "sync"], folder)
    assert status == 1 and len(out["problems"]) == 2           # also through the command


# ---- dev mode --------------------------------------------------------------------------

def test_dev_mode_sync_only_writes_the_agent_files(tmp_path):
    code = _code_copy(tmp_path)
    (code / "config" / "local.yaml").write_text("raw_roots: []\nprotected_paths: []\nagents:\n"
                                                "  harness: claude-code\n", encoding="utf-8")
    tracked = [p for p in code.rglob("*") if p.is_file()]
    before = {p: p.read_bytes() for p in tracked}
    status, out = _run(["project", "sync"], code, LABPIPE_CODE=str(code),
                       LABPIPE_PROJECT=str(code))
    assert status == 0 and out["mode"] == "dev" and "not rewritten" in out["note"]
    assert {row["file"] for row in out["written"]} >= {
        ".claude/agents/supervisor.md", ".claude/agents/xrd-analyst.md"}
    assert all(p.read_bytes() == before[p] for p in tracked)    # AGENTS.md, docs/, .claude/*.json …
    assert not (code / ".labpipe").exists()
    analyst = (code / ".claude" / "agents" / "xrd-analyst.md").read_text(encoding="utf-8")
    assert "`docs/methods/xrd.md`" in analyst and ".labpipe/" not in analyst   # as in dev mode
    status, out = _run(["agents", "sync"], code, LABPIPE_CODE=str(code), LABPIPE_PROJECT=str(code))
    assert status == 0 and out["mode"] == "dev"
    assert not (code / ".labpipe").exists()
    status, out = _run(["doctor"], code, LABPIPE_CODE=str(code), LABPIPE_PROJECT=str(code))
    assert [c for c in out["checks"] if c["check"] == "project"][0]["message"].count("dev mode")


def test_dev_mode_is_the_default_in_the_repository(monkeypatch):
    assert safety.is_dev(REPO) and safety.dev_mode()
    assert not safety.is_dev(REPO / "docs")


# ---- agents may not write generated files ----------------------------------------------

def _write(path: Path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}, "cwd": str(path.parent)}


def test_the_guard_refuses_writes_to_generated_files_and_labpipe(tmp_path, monkeypatch):
    folder = _in_project(monkeypatch, Path(_new(tmp_path)["project"]))
    config = safety.load_pipeline_config()
    for relative in ("AGENTS.md", "CLAUDE.md", ".labpipe/docs/SAFETY.md", ".labpipe/new.txt",
                     ".labpipe/generated.json", ".labpipe/hooks/guard.py",
                     ".claude/settings.json", ".claude/unattended.json",
                     ".claude/agents/supervisor.md", ".codex/hooks.json",
                     ".codex/rules/labpipe.rules"):
        reason = guard.decide(_write(folder / relative), config, "interactive")
        assert reason and "generated by labpipe" in reason and "lab/LAB.md" in reason, relative
    for relative in ("lab/LAB.md", "lab/agents/xrd-analyst.md", "config/samples.yaml",
                     "config/measurements/xrd/S-1_a.yaml", "analysis/S-1/xrd/m/notes.md"):
        assert guard.decide(_write(folder / relative), config, "interactive") is None, relative
    assert guard.decide(_write(folder / "analysis" / "S-1" / "x" / "results.json"), config,
                        "interactive")                      # still refused, for its own reason
    patch = {"tool_name": "apply_patch", "cwd": str(folder), "tool_input": {
        "command": "*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-a\n+b\n*** End Patch"}}
    assert "generated by labpipe" in guard.decide(patch, config, "interactive")


def test_the_guard_leaves_the_repository_alone_in_dev_mode(monkeypatch):
    monkeypatch.setattr(guard, "PROJECT_ROOT", REPO)
    for relative in ("AGENTS.md", "docs/SAFETY.md", ".claude/settings.json"):
        assert guard.decide(_write(REPO / relative), None, "interactive") is None


# ---- the whole thing in a project ------------------------------------------------------

def test_scan_analyze_report_and_replay_in_a_project_made_by_project_new(tmp_path):
    pytest.importorskip("numpy")      # the analysis side; the slim host install has none
    data = tmp_path / "data"
    (data / "XRD").mkdir(parents=True)
    positions = fcc_positions(3.8898)
    peaks = [(positions[0], 6000.0, 1.2, 0.6), (positions[1], 3000.0, 1.4, 0.6),
             (positions[2], 2200.0, 1.8, 0.6), (positions[3], 2000.0, 2.1, 0.6)]
    x, y = synthetic_pattern(peaks)
    (data / "XRD" / "S-9_scan.xrdml").write_text(xrdml_text(x, y, "S-9"), encoding="utf-8")
    raw_before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    repo_before = _repo_state()
    cwd = tmp_path / "work"
    cwd.mkdir()
    status, made = _run(["project", "new", str(cwd / "films"), "--data", str(data),
                         "--name", "films"], cwd, HOME=str(tmp_path / "home"))
    assert status == 0 and made["name"] == "films"
    folder = cwd / "films"
    with open(folder / "config" / "local.yaml", "a") as stream:
        stream.write("automation: {settle_minutes: 0, notify: {desktop: false}}\n")
    (folder / "config" / "lab.yaml").write_text("plots: {font_size: 8}\n", encoding="utf-8")
    sub = folder / "analysis"
    sub.mkdir()
    env = {"HOME": str(tmp_path / "home")}                       # no LABPIPE_* : found by folder
    status, scanned = _run(["scan"], sub, **env)                  # run from a subfolder
    assert status == 0 and scanned["counts"] == {"pending": 1}
    assert (folder / "manifest.json").exists() and not (sub / "manifest.json").exists()
    [row] = _run(["pending"], sub, **env)[1]
    status, done = _run(["xrd", "analyze", "--entry", row["entry_id"]], sub, **env)
    assert status == 0 and done["status"] == "done"
    out = folder / done["output_dir"]
    record = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    sources = {s["layer"].split()[0]: s for s in record["parameters"]["sources"]}
    assert sources["global"]["root"] == "code" and sources["global"]["file"] == "config/xrd.yaml"
    assert sources["lab"]["root"] == "project" and sources["lab"]["file"] == "config/lab.yaml"
    assert record["references"] and all(r["root"] == "code" for r in record["references"])
    assert not (folder / "config" / "xrd.yaml").exists()         # defaults are not copied
    status, built = _run(["report"], sub, **env)
    assert status == 0 and (folder / "analysis" / "report.html").exists()
    status, replayed = _run(["replay", "--force", str(out / "provenance.json")], sub, **env)
    assert status == 0 and replayed["all_outputs_identical"], replayed
    assert replayed["replay_dir"].startswith("logs/replay/")
    assert {p: p.read_bytes() for p in data.rglob("*") if p.is_file()} == raw_before
    # nothing of the project was written into the code folder
    assert _repo_state() == repo_before


def test_untouched_lab_template_adds_nothing_and_comments_are_left_out(tmp_path):
    from labpipe import agents
    template = (agents.CODE_ROOT / "templates" / "project" / "LAB.md").read_text(encoding="utf-8")
    assert agents.without_comments(template) == "# Conventions of this lab"   # heading only
    assert "Vulcan" not in agents.without_comments(template)
    text = "<!-- example: call it Vulcan -->\nWe answer in Croatian.\n"
    assert agents.without_comments(text) == "We answer in Croatian."


def test_generated_agents_md_says_nothing_yet_for_the_untouched_template(tmp_path):
    from labpipe import project
    folder = tmp_path / "p"
    (folder / "lab").mkdir(parents=True)
    (folder / "lab" / "LAB.md").write_text(
        (project.CODE_ROOT / "templates" / "project" / "LAB.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    text = project._with_additions("AGENTS.md", folder).decode("utf-8")
    assert text.rstrip().endswith("## This lab's additions (lab/LAB.md)\n\n(nothing yet)".rstrip())
    assert "Vulcan" not in text
