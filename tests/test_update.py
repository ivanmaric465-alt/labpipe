"""`labpipe update`, the parts that need no analysis: the preview's comparison, the release folder,
backups, the installer's command line, the questions, the guard, doctor, and the container
commands of the preview and the restart (a fake engine; nothing is built or started).
The whole thing with a fake origin, two releases and a real project is tests/test_update_flow.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from labpipe import container, doctor, guard, preview, release, safety, update
from tests.test_container import _config, _fake_code

REPO = Path(__file__).resolve().parents[1]


# ---- the comparison: numbers come from code ----------------------------------------------

def _row(eid, method, status="done", **quantities):
    return {"entry_id": eid, "method": method, "status": status, "quantities": {
        name: {"value": value, "sigma": sigma, "unit": unit}
        for name, (value, sigma, unit) in quantities.items()}}


def _rows(*rows):
    return {r["entry_id"]: r for r in rows}


def _manifest(**hashes):
    return {"entries": {eid: {"entry_id": eid, "method": "xrd", "status": "done",
                              "results_sha256": sha} for eid, sha in hashes.items()}}


def test_compare_gives_the_largest_change_and_whether_it_is_within_sigma():
    before = _rows(_row("a", "xrd", **{"xrd.a.fcc": (3.8898, 0.001, "Å"), "xrd.size.fcc": (5.0, 0.5, "nm")}),
                   _row("b", "xrd", **{"xrd.a.fcc": (3.9000, 0.001, "Å"), "xrd.size.fcc": (6.0, 0.5, "nm")}))
    after = _rows(_row("a", "xrd", **{"xrd.a.fcc": (3.8902, 0.001, "Å"), "xrd.size.fcc": (5.3, 0.5, "nm")}),
                  _row("b", "xrd", **{"xrd.a.fcc": (3.8996, 0.001, "Å"), "xrd.size.fcc": (6.9, 0.5, "nm")}))
    info = preview.compare_method("xrd", before, after, _manifest(a="1", b="2"), _manifest(a="3", b="4"))
    lattice, size = info["quantities"]["xrd.a.fcc"], info["quantities"]["xrd.size.fcc"]
    assert info["measurements"] == 2 and info["results_differ"] == 2
    assert lattice["max_abs_change"] == pytest.approx(0.0004) and lattice["within_sigma"] is True
    assert size["max_abs_change"] == pytest.approx(0.9) and size["within_sigma"] is False
    assert size["beyond_sigma"] == 1 and size["with_sigma"] == 2
    line = preview.describe_method("xrd", {**info, "status": "changed", "available": 2, "limit": None})
    assert line == ("xrd: 2 measurements; a.fcc changes up to 0.0004 Å (within σ for all); "
                    "size.fcc changes up to 0.9 nm (beyond σ for 1 of 2).")


def test_compare_reports_failures_new_and_gone_quantities_and_unchanged():
    before = _rows(_row("a", "eds", **{"eds.at.Pd": (50.0, None, "at%")}),
                   _row("b", "eds", status="failed"), _row("c", "eds", **{"eds.at.Pd": (10.0, None, "at%")}),
                   _row("d", "eds", **{"eds.at.Pd": (1.0, None, "at%")}))
    after = _rows(_row("a", "eds", **{"eds.at.Pd": (50.0, None, "at%"), "eds.at.Pt": (1.0, None, "at%")}),
                  _row("b", "eds", **{"eds.at.Pd": (1.0, None, "at%")}),
                  _row("c", "eds", status="failed"),
                  _row("d", "eds", status="stale"))               # not analysed again: left out
    info = preview.compare_method("eds", before, after, {"entries": {}}, {"entries": {}})
    assert info["measurements"] == 1             # a (b had no results, c fails now, d is not redone)
    assert info["no_longer_failing"] == ["b"] and info["now_failing"] == ["c"]
    assert info["only_after"] == ["eds.at.Pt"] and info["quantities"]["eds.at.Pd"]["changed"] == 0
    line = preview.describe_method("eds", {**info, "status": "changed", "available": 4, "limit": None})
    assert "the named quantities are unchanged" in line and "1 fail now: c" in line
    assert "1 no longer fail: b" in line and "+eds.at.Pt" in line
    assert preview.describe_method("sem", {"status": "unchanged"}) == \
        "sem: unchanged (the calculation is the same)."


def test_without_named_quantities_the_results_files_are_compared():
    info = preview.compare_method("sem", _rows(_row("a", "sem")), _rows(_row("a", "sem")),
                                  {"entries": {"a": {"entry_id": "a", "method": "sem", "results_sha256": "1"}}},
                                  {"entries": {"a": {"entry_id": "a", "method": "sem", "status": "done",
                                                     "results_sha256": "2"}}})
    assert info["results_differ"] == 1
    assert "1 results.json differ" in preview.describe_method(
        "sem", {**info, "status": "changed", "available": 1, "limit": None})


def test_the_limit_is_said():
    info = {"status": "changed", "measurements": 3, "available": 12, "limit": 3, "quantities": {},
            "results_differ": 0, "now_failing": [], "no_longer_failing": [], "only_before": [],
            "only_after": []}
    assert preview.describe_method("xrd", info).startswith("xrd: 3 measurements (the first 3 of 12);")


def test_changed_methods():
    assert preview.changed_methods({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "9", "d": "4"}) \
        == ["b", "c", "d"]


def test_pending_entries_of_the_scratch_project_take_no_part(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"entries": {
        "a": {"entry_id": "a", "status": "pending"}, "b": {"entry_id": "b", "status": "stale"}}}),
        encoding="utf-8")
    assert preview._park_pending(tmp_path) == 1
    entries = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["entries"]
    assert entries["a"]["status"] == "baseline" and entries["b"]["status"] == "stale"


# ---- the release folder ------------------------------------------------------------------

def _git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


def _clone_with_tags(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    _git(app, "init", "-q", "-b", "main")
    for tag in ("v0.2.0", "v0.3.0"):
        (app / "marker.txt").write_text(tag, encoding="utf-8")
        _git(app, "add", "-A")
        _git(app, "commit", "-q", "-m", tag)
        _git(app, "tag", tag)
    return app


def test_the_release_folder_is_made_once_and_reused(tmp_path):
    app, home = _clone_with_tags(tmp_path), tmp_path / "home"
    folder = preview.release_folder(app, home, "v0.2.0")
    assert folder == home / "releases" / "v0.2.0"
    assert (folder / "marker.txt").read_text(encoding="utf-8") == "v0.2.0"
    assert _git(folder, "rev-parse", "HEAD") == _git(app, "rev-parse", "v0.2.0^{commit}")
    (folder / "kept.txt").write_text("cache", encoding="utf-8")
    assert preview.release_folder(app, home, "v0.2.0") == folder
    assert (folder / "kept.txt").exists()                           # reused, not made again
    (home / "releases" / "v0.3.0").mkdir()
    (home / "releases" / "v0.3.0" / "x").write_text("?", encoding="utf-8")
    with pytest.raises(preview.PreviewError, match="not v0.3.0"):
        preview.release_folder(app, home, "v0.3.0")
    with pytest.raises(preview.PreviewError, match="not a release"):
        preview.release_folder(app, home, "v9.9.9")


# ---- backups and what is restored -------------------------------------------------------

def test_a_backup_is_never_overwritten_and_holds_what_an_update_may_change(tmp_path):
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "lab.yaml").write_text("a: 1\n", encoding="utf-8")
    (root / "labpipe.yaml").write_text("name: x\n", encoding="utf-8")
    (root / "manifest.json").write_text("{}\n", encoding="utf-8")
    (root / "lab").mkdir()
    (root / "lab" / "methods.yaml").write_text("methods: {}\n", encoding="utf-8")
    (root / "analysis").mkdir()
    first = update.new_backup_folder(root, "v0.2.0")
    copied = update.make_backup(root, first)
    assert first == root / "backups" / "v0.2.0"
    assert sorted(copied) == ["config/", "lab/methods.yaml", "labpipe.yaml", "manifest.json"]
    assert (first / "config" / "lab.yaml").read_text(encoding="utf-8") == "a: 1\n"
    assert not (first / "analysis").exists()
    second = update.new_backup_folder(root, "v0.2.0")
    update.make_backup(root, second)
    third = update.new_backup_folder(root, "v0.2.0")
    assert (second.name, third.name) == ("v0.2.0-2", "v0.2.0-3")
    update.make_backup(root, third)
    assert update.latest_backup(root, "v0.2.0") == third           # the newest, not v0.2.0-2
    assert update.latest_backup(root, "v0.9.0") is None
    (root / "backups" / "v0.2.0-x").mkdir()
    assert update.latest_backup(root, "v0.2.0") == third           # a stray folder is no backup


def test_restore_plan_lists_only_what_differs(tmp_path):
    root, source = tmp_path / "p", tmp_path / "b"
    (root / "config").mkdir(parents=True)
    (source / "config").mkdir(parents=True)
    (source / "config" / "same.yaml").write_text("a: 1\n", encoding="utf-8")
    (root / "config" / "same.yaml").write_text("a: 1\n", encoding="utf-8")
    (source / "config" / "lab.yaml").write_text("old: 1\n", encoding="utf-8")
    (root / "config" / "lab.yaml").write_text("new: 1\n", encoding="utf-8")
    (source / "config" / "gone.yaml").write_text("x: 1\n", encoding="utf-8")
    plan = update.restore_plan(root, source)
    assert [(i["file"], i["missing"]) for i in plan] == [("config/gone.yaml", True),
                                                         ("config/lab.yaml", False)]
    assert "-new: 1" in plan[1]["diff"] and "+old: 1" in plan[1]["diff"]


# ---- the installer, versions, changelog -------------------------------------------------

def test_the_installer_is_told_to_keep_the_checkout_and_the_mode_is_kept(tmp_path, monkeypatch):
    command, env = update.installer_command(tmp_path / "app", "full", windows=False)
    assert command == ["bash", str(tmp_path / "app" / "install.sh"), "--no-setup",
                       "--keep-checkout", "--full"]
    assert env["LABPIPE_CHANNEL"] == "keep" and env["LABPIPE_SWITCHED"] == "1"    # (older installers)
    assert env["LABPIPE_FULL"] == "1" and env["LABPIPE_NO_SETUP"] == "1"
    command, env = update.installer_command(tmp_path / "app", "host", windows=False)
    assert "--full" not in command and "LABPIPE_FULL" not in env
    assert update.installer_command(tmp_path / "app", "host", windows=True)[0][-2:] == [
        "-NoSetup", "-KeepCheckout"]


def test_the_install_mode_is_read_from_the_record_or_found_out(tmp_path):
    (tmp_path / "install.json").write_text('{"mode": "full"}\n', encoding="utf-8")
    assert update.install_mode(tmp_path) == "full"
    (tmp_path / "install.json").write_text('{"mode": "host"}\n', encoding="utf-8-sig")
    assert update.install_mode(tmp_path) == "host"
    (tmp_path / "install.json").write_text("garbage", encoding="utf-8")
    assert update.install_mode(tmp_path) in ("full", "host")      # asks the environment


def test_the_changes_are_shown_in_the_four_parts_and_a_rollback_says_what_is_undone():
    entries = release.parse_changelog(
        "# Changelog\n\n## Unreleased\n\n## v0.3.0 — 2026-12-01\n\n### Changes results\n\n"
        "- `xrd`: the background changes (why).\n\n### New\n\n- Something.\n\n"
        "## v0.2.0 — 2026-11-01\n\n### New\n\n- First.\n")
    shown = update.format_entries(release.between(entries, "v0.2.0", "v0.3.0"))
    assert shown[0] == "v0.3.0 — 2026-12-01"
    assert "  Changes results:" in shown and "    - `xrd`: the background changes (why)." in shown
    assert "  Settings migrated: none" in shown and "  Needs your action: none" in shown
    undone = update.format_entries(release.between(entries, "v0.3.0", "v0.2.0"))
    assert undone[0] == "v0.3.0 — 2026-12-01  (undone by going back)"
    assert update.results_bullets(release.between(entries, "v0.2.0", "v0.3.0")) == [
        "`xrd`: the background changes (why)."]
    assert update.tag_name("0.3.0") == "v0.3.0" and update.tag_name("v1.2.3") == "v1.2.3"


# ---- questions ---------------------------------------------------------------------------

def test_questions_default_to_no_and_need_a_terminal_unless_yes(monkeypatch, capsys):
    monkeypatch.setattr(update.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert update.ask("Go on?", True) is True
    assert "Go on? [y/N] y (--yes)" in capsys.readouterr().err        # says what it did
    with pytest.raises(update.UpdateError, match="--yes"):
        update.ask("Go on?", False)
    monkeypatch.setattr(update.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    for answer, expected in (("y", True), ("YES", True), ("", False), ("n", False), ("what", False)):
        monkeypatch.setattr("builtins.input", lambda answer=answer: answer)
        assert update.ask("Go on?", False) is expected


def test_dev_mode_refuses_with_a_short_message(monkeypatch, capsys):
    monkeypatch.setattr(update.safety, "dev_mode", lambda: True)
    args = SimpleNamespace(resume=None, check=True, preview=False, to=None, yes=False, limit=None)
    assert update.main(args) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "dev mode: use git; releases are for installations"}
    monkeypatch.setattr(update.safety, "dev_mode", lambda: False)
    monkeypatch.setattr(update.safety, "is_installation", lambda code: False)    # not an installation
    assert update.main(args) == 2 and "dev mode" in capsys.readouterr().out


def test_update_is_a_host_command_and_not_a_method_name():
    from labpipe import labmethods
    assert "update" in container.LOCAL_COMMANDS and "update" in labmethods.RESERVED
    assert container.forward_argv(["update", "--check"], "/p", "/p", "podman", "c", 1, 1,
                                  False) is None


# ---- agents never run it -----------------------------------------------------------------

def _bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_guard_refuses_update_in_unattended_runs_and_the_harness_asks_otherwise():
    config = {"raw_roots": [], "protected_paths": [], "analysis_dir": "analysis",
              "logs_dir": "logs", "manifest": "manifest.json"}
    for mode in ("unattended", "postdoc"):
        reason = guard.decide(_bash("labpipe update --yes"), config, mode)
        assert reason and ("may not update" in reason or "may only run" in reason)
    assert "may not update" in guard.decide(_bash("labpipe update --check"), config, "unattended")
    assert guard.decide(_bash("labpipe update"), config, "interactive") is None   # the harness asks
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert {"Bash(labpipe update)", "Bash(labpipe update *)"} <= set(settings["permissions"]["ask"])
    for name in ("unattended.json", "unattended-container.json"):
        denied = json.loads((REPO / ".claude" / name).read_text(encoding="utf-8"))["permissions"]["deny"]
        assert {"Bash(labpipe update)", "Bash(labpipe update *)"} <= set(denied)
    rules = (REPO / ".codex" / "rules" / "labpipe.rules").read_text(encoding="utf-8")
    assert 'pattern = ["labpipe", "update"]' in rules and 'decision = "prompt"' in rules


# ---- doctor ------------------------------------------------------------------------------

def test_doctor_says_when_the_project_was_set_up_with_another_release(monkeypatch):
    from labpipe import __version__, project
    monkeypatch.setattr(doctor.safety, "dev_mode", lambda: False)
    monkeypatch.setattr(project, "read_project", lambda *a: {"labpipe_version": "0.0.1"})
    (item,) = doctor._project_release()
    assert item["check"] == "project_release" and item["status"] == "warn"
    assert f"set up with labpipe 0.0.1; the installation is {__version__}: run labpipe update here" \
        in item["message"]
    assert "never you" in item["next"]
    monkeypatch.setattr(project, "read_project", lambda *a: {"labpipe_version": __version__})
    assert doctor._project_release() == []
    monkeypatch.setattr(project, "read_project", lambda *a: {})
    assert doctor._project_release() == []                            # unknown: no claim
    monkeypatch.setattr(project, "read_project", lambda *a: {"labpipe_version": "0.0.1"})
    monkeypatch.setattr(doctor.safety, "dev_mode", lambda: True)
    assert doctor._project_release() == []                            # dev mode: git


# ---- container mode: command lines, no engine --------------------------------------------

def test_the_image_of_a_release_is_built_from_its_folder(tmp_path, monkeypatch):
    code = _fake_code(tmp_path)
    cfg = _config(tmp_path)["container"]
    monkeypatch.setattr(container, "_code", lambda: REPO)             # the running code is another
    assert container.container_hash(code) != container.container_hash()
    assert container.image_tag(cfg, code) == f"localhost/labpipe:{container.container_hash(code)}"
    seen = {}

    def fake_run(command, cwd=None, **kwargs):
        seen["command"] = command
        staged = sorted(p.relative_to(cwd).as_posix() for p in Path(cwd).rglob("*") if p.is_file())
        seen["staged"] = staged
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(container.subprocess, "run", fake_run)
    tag = container.build("podman", cfg, code)
    assert tag == container.image_tag(cfg, code)
    assert seen["command"][:3] == ["podman", "build", "-f"] and tag in seen["command"]
    assert "src/labpipe/a.py" in seen["staged"] and "config/local.yaml" not in seen["staged"]


def test_the_preview_runs_each_command_in_a_throwaway_container(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    scratch = tmp_path / "home" / "preview" / "proj-v0.3.0"
    monkeypatch.setattr(safety, "PROJECT_ROOT", project)
    monkeypatch.setattr(safety, "CODE_ROOT", REPO)
    config = _config(tmp_path)
    args = container.run_args(config, "podman", tag="localhost/labpipe:abc", project=scratch,
                              command=["analyze", "--pending", "--method", "eds", "--limit", "3"])
    joined = " ".join(args)
    assert args[:3] == ["podman", "run", "--rm"] and "-d" not in args[:4]
    assert "--name" not in args and "--restart" not in args and "-p" not in args
    assert f"-v {scratch}:/project " in joined + " " and f"-v {tmp_path / 'data'}:/data/lab:ro" in joined
    assert "-home:" not in joined and "--secret" not in joined and "/opt/labpipe" not in joined.replace(
        "LABPIPE_CODE=/opt/labpipe", "")
    assert args[-8:] == ["localhost/labpipe:abc", "labpipe", "analyze", "--pending", "--method",
                         "eds", "--limit", "3"]
    assert f"LABPIPE_HOST_ROOT={container._host_path(scratch)}" in joined
    assert "LABPIPE_ALLOWED_HOSTS=" in joined and "github.com" not in joined        # no network
    # the long-running container of the project is as before
    keeping = container.run_args(config, "podman", tag="localhost/labpipe:abc")
    assert keeping[:4] == ["podman", "run", "-d", "--name"] and "--rm" not in keeping


def test_the_preview_builds_the_releases_image_only_when_it_is_missing(tmp_path, monkeypatch):
    project, release_dir, scratch = tmp_path / "p", _fake_code(tmp_path), tmp_path / "s"
    monkeypatch.setattr(safety, "PROJECT_ROOT", project)
    config = {**_config(tmp_path), "container": {**_config(tmp_path)["container"]}}
    monkeypatch.setattr(container, "local_container_block", lambda: {"engine": "podman"})
    monkeypatch.setattr(container, "load_config", lambda: config)
    monkeypatch.setattr(container, "current_engine", lambda: "podman")
    built, said, existing = [], [], {"image": False}
    monkeypatch.setattr(container, "image_exists", lambda engine, tag: existing["image"])
    monkeypatch.setattr(container, "build", lambda engine, cfg, code=None: built.append(code))
    runner = preview.Runner(project, scratch, release_dir, said.append)
    runner.prepare()
    assert built == [release_dir] and runner.tag == container.image_tag(config["container"], release_dir)
    existing["image"] = True
    runner.prepare()
    assert built == [release_dir]                                   # kept: not built again
    command = runner.command(["scan"])
    assert command[:3] == ["podman", "run", "--rm"] and command[-3:] == [runner.tag, "labpipe", "scan"]


def test_a_native_preview_uses_the_venvs_python_with_the_releases_code(tmp_path, monkeypatch):
    release_dir, scratch = tmp_path / "rel", tmp_path / "scratch"
    monkeypatch.setattr(container, "local_container_block", lambda: None)
    seen = {}

    def fake_run(command, cwd=None, env=None, **kwargs):
        seen.update(command=command, cwd=cwd, env=env)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(preview.subprocess, "run", fake_run)
    preview.Runner(tmp_path, scratch, release_dir, print).run(["scan"])
    assert seen["command"] == [sys.executable, "-m", "labpipe.cli", "scan"]
    assert seen["env"]["LABPIPE_CODE"] == str(release_dir) and seen["env"]["LABPIPE_PROJECT"] == str(scratch)
    assert seen["env"]["PYTHONPATH"].split(":")[0] == str(release_dir / "src") or \
        seen["env"]["PYTHONPATH"].split(";")[0] == str(release_dir / "src")
    assert seen["cwd"] == str(scratch)


def test_restarting_in_container_mode_replaces_the_container_and_doctor_is_summed_up(tmp_path, monkeypatch, capsys):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"started": "labpipe-x"}), stderr="")

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    monkeypatch.setattr(container, "local_container_block", lambda: {"engine": "podman"})
    monkeypatch.setattr(update, "_project_command", lambda root, args: SimpleNamespace(
        returncode=0, stderr="", stdout=json.dumps({"ready": False, "checks": [
            {"check": "container", "status": "todo", "message": "not running", "next": "start it"},
            {"check": "python", "status": "ok", "message": "fine", "next": None}]})))
    state = {"report": {}, "file": "x.json"}
    update.restart_step(state, tmp_path)
    assert commands[0][-3:] == ["container", "start", "--replace"] and commands[0][1:3] == ["-m", "labpipe.cli"]
    update.doctor_step(state, tmp_path)
    err = capsys.readouterr().err
    assert "1 item(s) need attention" in err and "[todo] not running  -> start it" in err
    assert state["report"]["doctor"]["ready"] is False
    # native mode: nothing to restart
    commands.clear()
    monkeypatch.setattr(container, "local_container_block", lambda: None)
    update.restart_step(state, tmp_path)
    assert commands == [] and "Native mode: nothing to restart" in capsys.readouterr().err
    # the container did not start: the message names the way on
    monkeypatch.setattr(container, "local_container_block", lambda: {"engine": "podman"})
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout='{"error": "start failed"}'))
    with pytest.raises(update.UpdateError, match="labpipe update --resume x.json"):
        update.restart_step(state, tmp_path)
