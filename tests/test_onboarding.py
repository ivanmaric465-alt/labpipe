"""The first conversation: the steps, the record, `labpipe onboarding`, the doctor item and the
guard (host-side: runs on Python 3.10 without numpy)."""

from __future__ import annotations

import json

import pytest

from labpipe import cli, container, doctor, guard, labmethods, onboarding, safety


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project that is not the code folder, with an empty logs folder."""
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(safety, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(onboarding.safety, "PROJECT_ROOT", tmp_path)
    return tmp_path


CONFIG = {"logs_dir": "logs", "analysis_dir": "analysis", "manifest": "manifest.json",
          "raw_roots": [], "protected_paths": []}


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(safety, "load_pipeline_config", lambda *a, **k: CONFIG)
    monkeypatch.setattr(cli, "load_pipeline_config", lambda *a, **k: CONFIG)
    monkeypatch.setattr(safety, "NO_PROJECT", False)
    monkeypatch.setattr(container, "forward", lambda argv: None)


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_steps_match_the_page():
    assert onboarding.doc_steps() == list(onboarding.STEPS)
    for step in onboarding.STEPS:
        assert onboarding.section(step).startswith(("## `" + step, "### `" + step))


def test_status_done_skip_restart(project, capsys):
    code, first = _run(capsys, "onboarding")
    assert code == 0 and first["next"] == "welcome" and not first["finished"]
    assert first["read"].endswith("ONBOARDING.md → welcome") and "five" in first["section"]
    code, after = _run(capsys, "onboarding", "done", "welcome")
    assert after["next"] == "files" and after["steps"][0]["state"] == "done"
    _, after = _run(capsys, "onboarding", "skip", "files")
    assert after["next"] == "methods" and after["steps"][1]["state"] == "skipped"
    state = json.loads((project / "logs" / "onboarding.json").read_text(encoding="utf-8"))
    assert state["schema"] == "labpipe.onboarding/1"
    assert state["steps"]["welcome"]["state"] == "done" and state["steps"]["welcome"]["utc"]
    _, after = _run(capsys, "onboarding", "skip", "all")
    assert after["finished"] and after["next"] is None
    _, after = _run(capsys, "onboarding", "restart", "--tour")
    assert after["next"] == "tour-report" and after["steps"][0]["state"] == "done" \
        and after["steps"][1]["state"] == "skipped"
    _, after = _run(capsys, "onboarding", "restart")
    assert after["next"] == "welcome" and after["handled"] == 0


def test_unknown_step_is_refused_with_the_list(project, capsys):
    code, out = _run(capsys, "onboarding", "done", "nonsense")
    assert code == 2 and "welcome" in out["error"] and "tour-updates" in out["error"]
    assert not (project / "logs" / "onboarding.json").exists()


def test_doctor_item_warns_and_never_blocks(project):
    [item] = doctor._onboarding(CONFIG)
    assert item["status"] == "warn" and item["check"] == "onboarding"
    assert item["message"] == f"onboarding: next step welcome (0 of {len(onboarding.STEPS)} done)"
    assert "ONBOARDING.md → welcome" in item["next"] and "labpipe onboarding done" in item["next"]
    onboarding.done("welcome", CONFIG)
    [item] = doctor._onboarding(CONFIG)
    assert "next step files (1 of" in item["message"]


def test_doctor_item_absent_when_finished_in_dev_mode_or_without_config(project, monkeypatch):
    assert doctor._onboarding(None) == []
    monkeypatch.setattr(safety, "dev_mode", lambda: True)
    assert doctor._onboarding(CONFIG) == []
    monkeypatch.setattr(safety, "dev_mode", lambda: False)
    onboarding.skip("all", CONFIG)
    assert doctor._onboarding(CONFIG) == []


def test_doctor_lists_it_last_and_ready_ignores_it(project, monkeypatch):
    monkeypatch.setattr(doctor, "_environment", lambda: [doctor._item("python", "ok", "p")])
    monkeypatch.setattr(doctor, "load_methods", lambda: {})
    monkeypatch.setattr(doctor, "_project", lambda: [])
    monkeypatch.setattr(doctor, "_project_release", lambda: [])
    monkeypatch.setattr(doctor, "_settings_migrations", lambda: [])
    monkeypatch.setattr(doctor, "load_pipeline_config", lambda: CONFIG)
    monkeypatch.setattr(doctor, "_raw_roots", lambda config: ([], []))
    monkeypatch.setattr(doctor, "_files", lambda config, usable: [])
    monkeypatch.setattr(doctor, "_method_setup", lambda: [])
    monkeypatch.setattr(doctor, "PROJECT_ROOT", project)
    (project / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(doctor.mf, "load", lambda config: {"entries": {}})
    monkeypatch.setattr(doctor, "_automation", lambda config: [])
    monkeypatch.setattr("labpipe.agents.status", lambda config: {
        "configured": True, "outdated": [], "harness": "claude-code", "models": {}})
    result = doctor.run()
    assert result["checks"][-1]["check"] == "onboarding"
    assert result["ready"] is True and result["next"]["check"] == "onboarding"


def test_guard_allows_it_interactively_but_not_in_unattended_runs():
    event = {"tool_name": "Bash", "tool_input": {"command": "labpipe onboarding done welcome"}}
    assert guard.decide(event, CONFIG, "interactive") is None
    assert guard.decide(event, CONFIG, "unattended")
    assert guard.decide(event, CONFIG, "postdoc")


def test_the_command_is_host_local_and_reserved(tmp_path):
    assert "onboarding" in container.LOCAL_COMMANDS and "onboarding" in labmethods.RESERVED
    assert container.forward_argv(["onboarding", "done", "welcome"], tmp_path, tmp_path,
                                  "podman", "labpipe", 1000, 1001, False) is None
