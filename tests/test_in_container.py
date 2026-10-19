"""Behaviour inside the container (LABPIPE_IN_CONTAINER=1) and unchanged outside it."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from labpipe import automation, provenance, replay, safety

REPO = Path(__file__).resolve().parents[1]
CONFIG = {"agents": {"harness": "claude-code"}, "logs_dir": "logs"}
CHOSEN = {"claude": "/host/bin/claude", "codex": "/host/bin/codex", "max_budget_usd": 5}


def _local(tmp_path, monkeypatch, roots="[{id: lab, path: /host/raw}, {id: other, path: /h/o}]",
           protected="[/host/sync]"):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pipeline.yaml").write_text("analysis_dir: analysis\n")
    (tmp_path / "config" / "local.yaml").write_text(
        f"raw_roots: {roots}\nprotected_paths: {protected}\n")
    monkeypatch.setattr(safety, "PROJECT_ROOT", tmp_path)


def test_root_paths_mapped_and_protected_dropped(tmp_path, monkeypatch):
    _local(tmp_path, monkeypatch)
    monkeypatch.setenv("LABPIPE_ROOT_PATHS", "lab=/data/lab,other=/data/other")
    config = safety.load_pipeline_config()
    assert [(r["id"], r["path"]) for r in config["raw_roots"]] == [
        ("lab", "/data/lab"), ("other", "/data/other")]
    assert config["protected_paths"] == []


def test_root_paths_unchanged_without_variable(tmp_path, monkeypatch):
    _local(tmp_path, monkeypatch)
    monkeypatch.delenv("LABPIPE_ROOT_PATHS", raising=False)
    config = safety.load_pipeline_config()
    assert config["raw_roots"][0]["path"] == "/host/raw"
    assert config["protected_paths"] == ["/host/sync"]


def test_root_paths_missing_and_unknown_id(tmp_path, monkeypatch):
    _local(tmp_path, monkeypatch)
    monkeypatch.setenv("LABPIPE_ROOT_PATHS", "lab=/data/lab")
    with pytest.raises(safety.SetupError, match="other"):
        safety.load_pipeline_config()
    monkeypatch.setenv("LABPIPE_ROOT_PATHS", "lab=/data/lab,other=/o,extra=/data/x")
    with pytest.raises(safety.SetupError, match="extra"):
        safety.load_pipeline_config()


def test_raw_root_override_still_works_inside(tmp_path, monkeypatch):
    _local(tmp_path, monkeypatch, roots="[{id: lab, path: /host/raw}]")
    monkeypatch.setenv("LABPIPE_ROOT_PATHS", "lab=/data/lab")
    monkeypatch.setenv("LABPIPE_RAW_ROOT", "/elsewhere")
    root = safety.load_pipeline_config()["raw_roots"][0]
    assert (root["id"], root["path"]) == ("lab", "/elsewhere")


def test_claude_command(monkeypatch):
    monkeypatch.delenv("LABPIPE_IN_CONTAINER", raising=False)
    outside = automation._harness_command(CONFIG, CHOSEN, Path("x"))
    assert outside[0] == "/host/bin/claude" and ".claude/unattended.json" in outside
    monkeypatch.setenv("LABPIPE_IN_CONTAINER", "1")
    inside = automation._harness_command(CONFIG, CHOSEN, Path("x"))
    assert inside[0] == "claude" and ".claude/unattended-container.json" in inside
    for flag in ("--setting-sources", "project", "--strict-mcp-config", "dontAsk",
                 "supervisor", "stream-json"):
        assert flag in inside


def test_container_settings_are_host_settings_without_sandbox():
    host = json.loads((REPO / ".claude" / "unattended.json").read_text())
    container = json.loads((REPO / ".claude" / "unattended-container.json").read_text())
    assert "sandbox" in host and "sandbox" not in container
    assert container == {k: v for k, v in host.items() if k != "sandbox"}


def test_codex_command(monkeypatch):
    from labpipe import agents
    config = {"agents": {"harness": "codex"}}
    monkeypatch.setattr(agents, "codex_trust", lambda *a, **k: "trusted")
    monkeypatch.setattr(agents, "load_roles", lambda: [{"role": "supervisor", "name": "s",
                                                        "body": "text"}])
    monkeypatch.delenv("LABPIPE_IN_CONTAINER", raising=False)
    outside = automation._harness_command(config, CHOSEN, Path("x"))
    assert outside[0] == "/host/bin/codex" and "workspace-write" in outside
    monkeypatch.setenv("LABPIPE_IN_CONTAINER", "1")
    inside = automation._harness_command(config, CHOSEN, Path("x"))
    assert inside[0] == "codex" and "danger-full-access" in inside
    assert "--dangerously-bypass-hook-trust" in inside
    monkeypatch.setattr(agents, "codex_trust", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="labpipe container login codex"):
        automation._harness_command(config, CHOSEN, Path("x"))


def test_codex_trust_reads_codex_home_and_project(tmp_path, monkeypatch):
    from labpipe import agents
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.toml").write_text(
        f"[projects.'{tmp_path}']\ntrust_level = \"trusted\"\n")   # literal string: Windows paths
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    assert agents.codex_trust(tmp_path) == "trusted"
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    assert agents.codex_trust(tmp_path) is None


def test_nightly_due():
    day = dt.date(2026, 10, 19)
    at = lambda h, m: dt.datetime(2026, 10, 19, h, m)  # noqa: E731
    assert not automation.nightly_due(at(1, 59), "02:00", None)          # before the time
    assert automation.nightly_due(at(2, 0), "02:00", None)               # at the time
    assert automation.nightly_due(at(2, 1), "02:00", day - dt.timedelta(days=1))
    assert not automation.nightly_due(at(2, 1), "02:00", day)            # already ran today
    assert automation.nightly_due(at(14, 30), "02:00", None)             # watcher started late
    assert not automation.nightly_due(at(23, 59), "02:00", day)


def test_last_nightly_from_log(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "PROJECT_ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    now = dt.datetime.now(dt.UTC)
    rows = [{"utc": "2020-01-01T00:00:00Z", "event": "nightly"},
            {"utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "run_finished",
             "trigger": "nightly"},
            {"utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "run_finished",
             "trigger": "manual"}]
    (tmp_path / "logs" / "automation.log").write_text("\n".join(map(json.dumps, rows)) + "\n")
    assert automation._last_nightly(CONFIG) == now.astimezone().date()
    (tmp_path / "logs" / "automation.log").write_text("")
    assert automation._last_nightly(CONFIG) is None


def test_provenance_runtime(monkeypatch):
    for name in ("LABPIPE_IN_CONTAINER", "LABPIPE_ENGINE", "LABPIPE_IMAGE_ID"):
        monkeypatch.delenv(name, raising=False)
    assert provenance.runtime() == {"mode": "native", "engine": None, "image_id": None}
    monkeypatch.setenv("LABPIPE_IN_CONTAINER", "1")
    monkeypatch.setenv("LABPIPE_ENGINE", "podman 5.8.7")
    monkeypatch.setenv("LABPIPE_IMAGE_ID", "sha256:abc")
    assert provenance.runtime() == {"mode": "container", "engine": "podman 5.8.7",
                                    "image_id": "sha256:abc"}


def test_replay_resolves_input_through_raw_root(tmp_path):
    raw = tmp_path / "data" / "lab"
    (raw / "XRD").mkdir(parents=True)
    (raw / "XRD" / "a.xy").write_text("1 2")
    config = {"raw_roots": [{"id": "lab", "path": str(raw)}]}
    recorded = {"path": "/host/gone/XRD/a.xy", "relative_path": "XRD/a.xy", "raw_root": "lab"}
    assert replay._input_path(recorded, config) == raw / "XRD" / "a.xy"
    assert replay._input_path({**recorded, "path": str(raw / "XRD" / "a.xy")},
                              {"raw_roots": []}) == raw / "XRD" / "a.xy"
    unknown = {**recorded, "raw_root": "nope"}
    assert replay._input_path(unknown, config) == Path("/host/gone/XRD/a.xy")
