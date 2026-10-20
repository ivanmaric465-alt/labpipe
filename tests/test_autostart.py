"""The container starts at login (autostart.py), `automation setup`, the old native units,
and what is left of notify. Nothing here touches the real systemd, engine or unit files:
conftest.py fakes `systemctl` and the unit folder, and each test fakes what else it needs."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from labpipe import autostart, automation, container, doctor, installer


class Systemctl:
    """A fake `systemctl --user`: records every call; `enabled` is what is-enabled says."""

    def __init__(self, enabled: bool = False):
        self.calls: list[tuple[str, ...]] = []
        self.enabled = enabled

    def __call__(self, *words: str) -> subprocess.CompletedProcess:
        self.calls.append(words)
        out = ("enabled" if self.enabled else "disabled") if words[0] == "is-enabled" else ""
        return subprocess.CompletedProcess(["systemctl", *words], 0, out, "")


@pytest.fixture
def systemctl(monkeypatch):
    fake = Systemctl()
    monkeypatch.setattr(autostart, "_systemctl", fake)
    return fake


@pytest.fixture
def units(monkeypatch, tmp_path):
    folder = tmp_path / "systemd-user"
    folder.mkdir()
    monkeypatch.setattr(autostart, "unit_dir", lambda: folder)
    return folder


@pytest.fixture
def terminal(monkeypatch):
    """A terminal whose answers come from `answers` (substring of the question → reply);
    everything else (the timing questions) takes its default. Returns (asked, saved)."""
    asked: list[str] = []
    saved: dict = {}
    answers: dict[str, str] = {}

    class Stdin:
        def isatty(self):
            return True

    def fake_input(prompt=""):
        asked.append(prompt)
        return next((reply for key, reply in answers.items() if key in prompt), "")

    monkeypatch.setattr(sys, "stdin", Stdin())
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(automation, "set_local_block", lambda name, value: saved.update({name: value}))
    monkeypatch.setattr(automation, "load_pipeline_config", lambda: {})
    monkeypatch.setattr(container, "container_config", lambda config: {"name": "labpipe-x"})
    monkeypatch.setattr(container, "running", lambda engine, name: False)
    return asked, saved, answers


def _container_mode(monkeypatch, engine="podman", system="Linux"):
    monkeypatch.setattr(container, "container_mode", lambda: True)
    monkeypatch.setattr(container, "current_engine", lambda: engine)
    monkeypatch.setattr(autostart.platform, "system", lambda: system)


def _write_units(folder, names=autostart.OLD_UNITS + (container.UNIT_NAME,)):
    for name in names:
        (folder / name).write_text("[Unit]\n", encoding="utf-8")


# ---- automation setup --------------------------------------------------------------------

def test_setup_podman_enables_podman_restart_only_after_a_yes(monkeypatch, terminal, systemctl,
                                                               capsys):
    asked, saved, answers = terminal
    _container_mode(monkeypatch)
    answers["Enable podman-restart.service"] = "y"
    assert automation.setup({"automation": {"claude": "/x/claude"}}) == 0
    assert ("enable", "podman-restart.service") in systemctl.calls
    assert saved["automation"]["claude"] == "/x/claude"          # other keys kept
    assert saved["automation"]["nightly_at"] == "02:00"
    out = capsys.readouterr().out
    assert "loginctl enable-linger $USER" in out                 # shown ...
    assert not any("linger" in " ".join(call) for call in systemctl.calls)   # ... never run


def test_setup_podman_without_a_yes_enables_nothing(monkeypatch, terminal, systemctl, capsys):
    _container_mode(monkeypatch)
    assert automation.setup({}) == 0
    assert not any(call[0] == "enable" for call in systemctl.calls)
    assert "Not enabled" in capsys.readouterr().out


def test_setup_podman_already_enabled_asks_nothing(monkeypatch, terminal, capsys):
    asked, _, _ = terminal
    fake = Systemctl(enabled=True)
    monkeypatch.setattr(autostart, "_systemctl", fake)
    _container_mode(monkeypatch)
    automation.setup({})
    assert not any(call[0] == "enable" for call in fake.calls)
    assert not any("Enable podman-restart" in q for q in asked)


def test_setup_docker_explains_and_runs_nothing(monkeypatch, terminal, systemctl, capsys):
    _container_mode(monkeypatch, engine="docker")
    assert automation.setup({}) == 0
    out = capsys.readouterr().out
    assert "Docker restarts the container" in out and "Start Docker Desktop when you sign in" in out
    assert systemctl.calls == []


def test_setup_podman_machine_is_marked_untested(monkeypatch, terminal, systemctl, capsys):
    _container_mode(monkeypatch, system="Darwin")
    automation.setup({})
    assert "UNTESTED" in capsys.readouterr().out and systemctl.calls == []


def test_setup_restarts_a_running_container_only_after_a_yes(monkeypatch, terminal, systemctl):
    _, _, answers = terminal
    _container_mode(monkeypatch, engine="docker")
    monkeypatch.setattr(container, "running", lambda engine, name: True)
    started = []
    monkeypatch.setattr(container, "cmd_start", lambda args: started.append(args.replace) or 0)
    automation.setup({})
    assert started == []
    answers["Restart it now"] = "y"
    automation.setup({})
    assert started == [True]


def test_setup_removes_the_old_units_only_after_a_yes_and_keeps_the_proxy(
        monkeypatch, terminal, systemctl, units, capsys):
    _, _, answers = terminal
    _container_mode(monkeypatch, engine="docker")
    _write_units(units)
    automation.setup({})                                          # no yes
    assert len(list(units.iterdir())) == 4 and systemctl.calls == []
    answers["Remove them now"] = "y"
    automation.setup({})
    assert [p.name for p in units.iterdir()] == [container.UNIT_NAME]
    assert ("disable", "--now", *autostart.OLD_UNITS) in systemctl.calls
    assert ("daemon-reload",) in systemctl.calls
    assert container.UNIT_NAME not in " ".join(" ".join(c) for c in systemctl.calls)


def test_setup_in_native_mode_says_it_needs_the_container(monkeypatch, terminal, systemctl, capsys):
    _, saved, _ = terminal
    monkeypatch.setattr(container, "container_mode", lambda: False)
    assert automation.setup({}) == 0
    out = capsys.readouterr().out
    assert "needs container mode" in out and "labpipe automation run" in out
    assert "process the new data" in out
    assert saved == {} and systemctl.calls == []


def test_setup_refuses_without_a_terminal_and_inside_the_container(monkeypatch, terminal, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert automation.setup({}) == 2
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("LABPIPE_IN_CONTAINER", "1")
    assert automation.setup({}) == 2
    assert "on the host" in capsys.readouterr().out


def test_watch_is_refused_outside_the_container(capsys):
    with pytest.raises(SystemExit) as stop:
        automation.watch({})
    assert stop.value.code == 2
    assert "runs in the project's container" in capsys.readouterr().err


# ---- the installer -----------------------------------------------------------------------

def test_installer_start_offers_autostart_without_asking_again(monkeypatch, tmp_path, systemctl,
                                                                capsys):
    monkeypatch.setattr(autostart.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer, "_labpipe", lambda args, cwd, capture=False:
                        subprocess.CompletedProcess(args, 0, json.dumps({"page": "http://p"}), ""))
    info = {"data": [], "baseline": False}
    installer._start(tmp_path, "claude-code", False, info, False, "podman", True)
    assert ("enable", "podman-restart.service") in systemctl.calls
    assert "loginctl enable-linger $USER" in capsys.readouterr().out
    systemctl.calls.clear()
    installer._start(tmp_path, "claude-code", False, info, False, "podman", False)
    assert systemctl.calls == []
    assert "does not start by itself" in capsys.readouterr().out
    installer._start(tmp_path, "claude-code", False, info, False, "docker", True)
    assert systemctl.calls == []


# ---- doctor ------------------------------------------------------------------------------

def test_autostart_check_per_engine(monkeypatch):
    monkeypatch.setattr(autostart, "_systemctl", Systemctl(enabled=True))
    assert autostart.check("podman", "Linux")[0] == "ok"
    monkeypatch.setattr(autostart, "_systemctl", Systemctl(enabled=False))
    status, message, step = autostart.check("podman", "Linux")
    assert status == "warn" and "does not start by itself at login" in message
    assert step == "labpipe automation setup"
    assert autostart.check("docker", "Linux")[0] == "ok"
    status, message, _ = autostart.check("podman", "Windows")
    assert status == "warn" and "untested" in message


def test_doctor_container_items_include_autostart(monkeypatch):
    state = {"name": "labpipe", "engine": "podman", "running": True, "image_up_to_date": True,
             "claude_login": True, "page": "http://localhost:1/?token=t", "next": None}
    monkeypatch.setattr(container, "status_record", lambda: dict(state))
    monkeypatch.setattr(doctor, "load_pipeline_config", lambda: {"agents": {}})
    monkeypatch.setattr(autostart.platform, "system", lambda: "Linux")
    items = {i["check"]: i for i in doctor._container()}
    assert items["autostart"]["status"] == "warn"    # the conftest fake says "unknown"
    monkeypatch.setattr(autostart, "_systemctl", Systemctl(enabled=True))
    assert {i["check"]: i for i in doctor._container()}["autostart"]["status"] == "ok"


def test_doctor_warns_about_old_units_with_the_removal_commands(units):
    assert doctor._old_automation() == []
    _write_units(units)
    [item] = doctor._old_automation()
    assert item["check"] == "old_automation" and item["status"] == "warn"
    assert "systemctl --user disable --now labpipe-watch.service labpipe-nightly.service " \
           "labpipe-nightly.timer" in item["next"]
    assert "daemon-reload" in item["next"] and container.UNIT_NAME not in item["next"]
    assert item["message"].count("labpipe-dbus-proxy") == 0


# ---- notify ------------------------------------------------------------------------------

def _notify_config(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("labpipe.safety.PROJECT_ROOT", tmp_path)
    return {"logs_dir": "logs", "analysis_dir": "analysis", "manifest": "manifest.json", "protected_paths": [],
            "raw_roots": [], "automation": {}}


def test_notify_records_and_uses_notify_send_on_linux(monkeypatch, tmp_path):
    config = _notify_config(tmp_path, monkeypatch)
    ran = []
    monkeypatch.setattr(automation.platform, "system", lambda: "Linux")
    monkeypatch.setattr(automation.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(automation.subprocess, "run", lambda command, **kw: ran.append(command)
                        or subprocess.CompletedProcess(command, 0))
    assert automation.notify(config, "labpipe: hello", kind="finished") == ["desktop"]
    assert ran == [["notify-send", "--app-name=labpipe", "labpipe", "labpipe: hello"]]
    line = json.loads((tmp_path / "logs" / "notifications.jsonl").read_text(encoding="utf-8"))
    assert line["message"] == "labpipe: hello" and line["kind"] == "finished"


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_notify_has_no_native_popups_on_macos_and_windows(monkeypatch, tmp_path, system):
    config = _notify_config(tmp_path, monkeypatch)
    monkeypatch.setattr(automation.platform, "system", lambda: system)
    monkeypatch.setattr(automation.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(automation.subprocess, "run", lambda *a, **k: pytest.fail("ran " + str(a)))
    assert automation.notify(config, "labpipe: hello") == []
    assert (tmp_path / "logs" / "notifications.jsonl").is_file()
