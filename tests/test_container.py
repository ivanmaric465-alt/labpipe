"""Container commands: command construction and forwarding, no real engine."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from labpipe import container as ct

REPO = Path(__file__).resolve().parents[1]


def _config(tmp_path, harness="claude-code", roots=None):
    return {"container": {"name": "labpipe", "image": "localhost/labpipe", "port": 8765,
                          "allowed_hosts": {"common": ["github.com"], "claude-code": ["a.example"],
                                            "codex": ["o.example"]}},
            "agents": {"harness": harness},
            "raw_roots": roots or [{"id": "lab", "path": str(tmp_path / "ro"),
                                    "source": str(tmp_path / "data")}]}


def _run(config, engine, **kw):
    return ct.run_args(config, engine, tag="localhost/labpipe:abc", image_id="sha256:1",
                       engine_text=f"{engine} 5.8.7", **kw)


def _fake_code(tmp_path) -> Path:
    """A small code folder: what the image copies, and the lab's files it must not."""
    code = tmp_path / "code"
    files = {"container/Dockerfile": "FROM x\n", "pyproject.toml": "[project]\n",
             "src/labpipe/a.py": "x = 1\n", "src/labpipe/__pycache__/a.pyc": "junk",
             "config/xrd.yaml": "a: 1\n", "config/plots.yaml": "b: 1\n",
             "config/pipeline.yaml": "c: 1\n", "config/lab.example.yaml": "# example\n",
             "config/local.yaml": "raw_roots: []\n", "config/lab.yaml": "xrd: {}\n",
             "config/samples.yaml": "samples: {}\n", "config/sample_ids.yaml": "{}\n",
             "config/measurements/xrd/S-1_a.yaml": "x: 1\n",
             "methods.yaml": "methods: {}\n", "AGENTS.md": "# a\n", "CLAUDE.md": "@AGENTS.md\n",
             "agents/supervisor.md": "---\nname: supervisor\n---\n", "docs/SAFETY.md": "# s\n",
             "references/xrd/phases.yaml": "phases: []\n", "templates/context/context.md": "#\n",
             "hooks/guard.py": "pass\n", ".claude/settings.json": "{}\n",
             ".codex/config.toml": "a = 1\n", ".codex/agents/x.toml": "generated\n",
             "analysis/S-1/results.json": "{}\n", "manifest.json": "{}\n", "context/c.md": "#\n"}
    for name, text in files.items():
        (code / name).parent.mkdir(parents=True, exist_ok=True)
        (code / name).write_text(text)
    return code


def test_image_holds_the_code_and_not_the_labs_files(tmp_path):
    files = set(ct.image_files(_fake_code(tmp_path)))
    assert {"container/Dockerfile", "pyproject.toml", "src/labpipe/a.py", "config/xrd.yaml",
            "config/plots.yaml", "config/pipeline.yaml", "config/lab.example.yaml",
            "methods.yaml", "AGENTS.md", "CLAUDE.md", "agents/supervisor.md", "docs/SAFETY.md",
            "references/xrd/phases.yaml", "templates/context/context.md", "hooks/guard.py",
            ".claude/settings.json", ".codex/config.toml"} <= files
    for lab in ("config/local.yaml", "config/lab.yaml", "config/samples.yaml",
                "config/sample_ids.yaml", "config/measurements/xrd/S-1_a.yaml", "manifest.json",
                "analysis/S-1/results.json", "context/c.md", ".codex/agents/x.toml",
                "src/labpipe/__pycache__/a.pyc"):
        assert lab not in files


def test_container_hash_covers_every_file_copied_into_the_image(tmp_path, monkeypatch):
    code = _fake_code(tmp_path)
    monkeypatch.setattr(ct.safety, "CODE_ROOT", code)
    first = ct.container_hash()
    assert len(first) == 12 and first == ct.container_hash()
    for name in ("container/Dockerfile", "src/labpipe/a.py", "docs/SAFETY.md", "methods.yaml",
                 "config/xrd.yaml", "AGENTS.md", "references/xrd/phases.yaml",
                 "hooks/guard.py", ".claude/settings.json", ".codex/config.toml"):
        original = (code / name).read_text()
        (code / name).write_text(original + "changed\n")
        assert ct.container_hash() != first, name
        (code / name).write_text(original)
    (code / "config" / "lab.yaml").write_text("changed: true\n")   # the lab's own: not in the image
    (code / "manifest.json").write_text("changed")
    assert ct.container_hash() == first


def test_build_context_has_exactly_the_image_files(tmp_path, monkeypatch):
    code = _fake_code(tmp_path)
    monkeypatch.setattr(ct.safety, "CODE_ROOT", code)
    staged = ct.stage_context(tmp_path / "ctx")
    found = {p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()}
    assert found == set(ct.image_files(code))


def test_run_args_podman(tmp_path):
    args = _run(_config(tmp_path), "podman", claude_login=True)
    joined = " ".join(args)
    assert args[:3] == ["podman", "run", "-d"] and args[-1] == "localhost/labpipe:abc"
    assert "--userns=keep-id --user 0:0" in joined
    assert f"-v {tmp_path / 'data'}:/data/lab:ro" in joined
    assert str(tmp_path / "ro") not in joined  # source preferred over path
    assert ":z" not in joined.lower() and "label=disable" in joined
    assert "LABPIPE_ROOT_PATHS=lab=/data/lab" in joined
    assert "LABPIPE_ALLOWED_HOSTS=github.com,a.example" in joined
    assert "-p 127.0.0.1:8765:8765" in joined
    assert "--secret labpipe-claude-token,type=env,target=CLAUDE_CODE_OAUTH_TOKEN" in joined
    assert "LABPIPE_ENGINE=podman 5.8.7" in joined and "LABPIPE_IMAGE_ID=sha256:1" in joined


def test_run_args_docker_codex(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "claude_env_file", lambda: Path("/home/u/.config/labpipe/claude.env"))
    args = _run(_config(tmp_path, harness="codex", roots=[{"id": "lab", "path": "~/raw"}]),
                "docker", claude_login=True)
    joined = " ".join(args)
    assert "keep-id" not in joined and "--user" not in args
    assert f"--env-file {Path('/home/u/.config/labpipe/claude.env')}" in joined
    assert "--secret" not in joined
    assert "LABPIPE_ALLOWED_HOSTS=github.com,o.example" in joined
    assert f"{Path('~/raw').expanduser()}:/data/lab:ro" in joined
    assert "--env-file" not in " ".join(_run(_config(tmp_path), "docker"))
    caps = [a for a in args if a.startswith("--cap-add")]       # docker: one flag each
    assert "--cap-add=NET_ADMIN" in caps and all("," not in a for a in caps)


@pytest.mark.parametrize("bad", ["a/b", "..", "x y", "", "a:b"])
def test_unsafe_root_id_refused(tmp_path, bad):
    with pytest.raises(ct.ContainerError):
        _run(_config(tmp_path, roots=[{"id": bad, "path": "/x"}]), "podman")


def _fwd(argv, cwd, repo, tty=False):
    return ct.forward_argv(argv, cwd, repo, "podman", "labpipe", 1000, 1001, tty)


def test_local_commands_not_forwarded(tmp_path):
    for argv in (["guard"], ["container", "start"], ["shell"], ["doctor"], ["setup"], ["init"],
                 ["agents", "sync"], ["methods", "check", "x"], ["scan", "--help"], []):
        assert _fwd(argv, tmp_path, tmp_path) is None


def test_forward_cwd_and_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "m.yaml").write_text("x")
    inside = _fwd(["scan"], tmp_path / "sub", tmp_path, tty=True)
    assert inside == ["podman", "exec", "-i", "-t", "--user", "1000:1001", "-w",
                      "/project/sub", "labpipe", "labpipe", "scan"]
    outside_cwd = _fwd(["status"], tmp_path.parent, tmp_path)
    assert "-t" not in outside_cwd and outside_cwd[outside_cwd.index("-w") + 1] == "/project"
    cmd = _fwd(["replay", "m.yaml", f"--out={tmp_path}/a", "S-1"], tmp_path / "sub", tmp_path)
    assert cmd[-3:] == ["/project/sub/m.yaml", "--out=/project/a", "S-1"]


def test_native_watcher_refused_in_container_mode(tmp_path):
    for argv in (["automation", "watch"], ["automation", "setup"]):
        with pytest.raises(ct.ForwardError, match="the container runs the watcher"):
            _fwd(argv, tmp_path, tmp_path)
    assert _fwd(["automation", "run"], tmp_path, tmp_path)[-3:] == ["labpipe", "automation", "run"]


def test_forward_refuses_outside_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "elsewhere.json"   # absolute on every OS (Windows: with a drive)
    with pytest.raises(ct.ForwardError, match="outside the project"):
        _fwd(["replay", str(outside)], repo, repo)
    with pytest.raises(ct.ForwardError):
        _fwd(["replay", "../"], repo, repo)


def test_main_without_container_block_runs_locally(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.yaml").write_text("raw_roots: []\n")
    monkeypatch.setattr(ct.safety, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LABPIPE_IN_CONTAINER", raising=False)
    assert ct.local_container_block() is None
    assert ct.forward(["scan"]) is None
    shutil.copy(REPO / "methods.yaml", tmp_path / "methods.yaml")
    env = dict(os.environ, LABPIPE_ROOT=str(tmp_path), PYTHONPATH=str(REPO / "src"))


def test_forward_runs_in_container_when_on(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.yaml").write_text("container:\n  engine: podman\n")
    monkeypatch.setattr(ct.safety, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LABPIPE_IN_CONTAINER", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ct.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(ct, "running", lambda engine, name: False)
    assert ct.forward(["scan"]) == 2
    calls = []
    monkeypatch.setattr(ct, "running", lambda engine, name: True)
    monkeypatch.setattr(ct.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 7))
    assert ct.forward(["scan"]) == 7 and calls[0][:2] == ["podman", "exec"]
    monkeypatch.setenv("LABPIPE_IN_CONTAINER", "1")
    assert ct.forward(["scan"]) is None


# ---- pop-ups, page token ----------------------------------------------------------------

def _popup_config(tmp_path, **extra):
    config = _config(tmp_path)
    config["container"].update(extra)
    return config


def test_run_args_port_and_token_always_present(tmp_path):
    joined = " ".join(_run(_popup_config(tmp_path, page_token="tok"), "podman", runtime_dir="/run/user/1"))
    assert "LABPIPE_PORT=8765" in joined and "LABPIPE_PAGE_TOKEN=tok" in joined
    assert "DBUS_SESSION_BUS_ADDRESS" not in joined and "labpipe-bus" not in joined


def test_run_args_popups_on(tmp_path):
    args = _run(_popup_config(tmp_path, popups=True), "docker", runtime_dir="/run/user/1000")
    joined = " ".join(args)
    assert "-v /run/user/1000/labpipe-bus:/run/labpipe-bus" in joined
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/labpipe-bus/bus" in joined
    assert ":z" not in joined.lower()


def test_run_args_popups_off_and_non_linux(tmp_path, monkeypatch):
    assert "labpipe-bus" not in " ".join(
        _run(_popup_config(tmp_path, popups=False), "podman", runtime_dir="/run/user/1000"))
    monkeypatch.setattr(ct.sys, "platform", "darwin")
    joined = " ".join(_run(_popup_config(tmp_path, popups=True), "podman"))
    assert "labpipe-bus" not in joined and "DBUS_SESSION_BUS_ADDRESS" not in joined


def test_proxy_unit_text():
    text = ct.proxy_unit_text()
    assert ("ExecStart=/usr/bin/xdg-dbus-proxy unix:path=%t/bus %t/labpipe-bus/bus --filter "
            "--talk=org.freedesktop.Notifications\n") in text
    assert "ExecStartPre=/usr/bin/mkdir -p %t/labpipe-bus" in text
    assert "Restart=on-failure" in text and "WantedBy=default.target" in text


def test_ensure_page_token_keeps_other_keys(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    local = tmp_path / "config" / "local.yaml"
    local.write_text("raw_roots: []\ncontainer:\n  engine: podman\n  popups: true\n")
    shutil.copy(REPO / "config" / "pipeline.yaml", tmp_path / "config" / "pipeline.yaml")
    monkeypatch.setattr(ct.safety, "PROJECT_ROOT", tmp_path)
    assert ct.ensure_page_token() is True
    block = ct.local_container_block()
    assert block["engine"] == "podman" and block["popups"] is True and len(block["page_token"]) >= 16
    assert "raw_roots" in local.read_text()
    token = block["page_token"]
    assert ct.ensure_page_token() is False
    assert ct.local_container_block()["page_token"] == token


def test_doctor_reports_container_state(monkeypatch):
    from labpipe import doctor
    state = {"name": "labpipe", "engine": "podman", "running": True, "image_up_to_date": True,
             "claude_login": False, "page": "http://localhost:8765/?token=t", "next": None}
    monkeypatch.setattr(ct, "status_record", lambda: dict(state))
    monkeypatch.setattr(doctor, "load_pipeline_config",
                        lambda: {"agents": {"harness": "claude-code"}})
    items = {i["check"]: i for i in doctor._container()}
    assert items["container"]["status"] == "ok" and "token=t" in items["container"]["message"]
    assert items["container_login"]["status"] == "todo"
    state.update(running=False, next="labpipe container start")
    [item] = doctor._container()
    assert item["status"] == "todo" and item["next"] == "labpipe container start"


def test_commands_refuse_without_container_block(monkeypatch):
    monkeypatch.setattr(ct, "local_container_block", lambda: None)
    with pytest.raises(ct.ContainerError, match="labpipe container setup"):
        ct._prepare()


# ---- one container per project ----------------------------------------------------------

def _project_root(tmp_path, monkeypatch, name="Pd films", local="raw_roots: []\n"):
    """A project folder that is not the code folder (the repository is the code)."""
    project = tmp_path / "proj"
    (project / "config").mkdir(parents=True)
    (project / "labpipe.yaml").write_text(f"schema: labpipe.project/1\nname: {name}\n")
    (project / "config" / "local.yaml").write_text(local)
    monkeypatch.setattr(ct.safety, "PROJECT_ROOT", project)
    monkeypatch.setattr(ct.safety, "CODE_ROOT", REPO)
    return project


def test_project_is_mounted_read_write_and_the_code_is_baked(tmp_path, monkeypatch):
    project = _project_root(tmp_path, monkeypatch)
    joined = " ".join(_run(_config(tmp_path), "podman"))
    assert f"-v {project}:/project " in joined + " " and "-w /project" in joined
    assert "LABPIPE_PROJECT=/project" in joined and "LABPIPE_CODE=/opt/labpipe" in joined
    assert ":/opt/labpipe" not in joined          # baked into the image, not mounted
    assert "/project:ro" not in joined and ":z" not in joined.lower()
    assert "label=disable" in joined and "labpipe.container_dev" not in joined
    assert f"-v {tmp_path / 'data'}:/data/lab:ro" in joined          # data stays read-only


def test_dev_mounts_the_code_folder_too(tmp_path, monkeypatch):
    project = _project_root(tmp_path, monkeypatch)
    joined = " ".join(_run(_config(tmp_path), "podman", dev=True))
    assert f"-v {project}:/project" in joined and f"-v {REPO}:/opt/labpipe" in joined
    assert "labpipe.container_dev=1" in joined


def test_a_repository_that_is_its_own_project_mounts_the_code_without_a_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(ct.safety, "PROJECT_ROOT", REPO)
    monkeypatch.setattr(ct.safety, "CODE_ROOT", REPO)
    joined = " ".join(_run(_config(tmp_path), "podman"))
    assert f"-v {REPO}:/project" in joined and f"-v {REPO}:/opt/labpipe" in joined


def test_each_project_has_its_own_name_volume_and_host_folder(tmp_path, monkeypatch):
    project = _project_root(tmp_path, monkeypatch, name="Pd films")
    cfg = ct.container_config({"container": {"name": "labpipe", "port": 8765}})
    assert cfg["name"] == "labpipe-pd-films"
    args = _run({**_config(tmp_path), "container": cfg}, "podman")
    joined = " ".join(args)
    assert "--name labpipe-pd-films" in joined and "-v labpipe-pd-films-home:/home/labpipe" in joined
    assert f"LABPIPE_HOST_ROOT={ct._host_path(project)}" in joined
    (project / "config" / "local.yaml").write_text("container: {name: mine}\n")
    assert ct.container_config({})["name"] == "mine"          # the project's own choice wins
    monkeypatch.setattr(ct.safety, "PROJECT_ROOT", REPO)       # the repository: as before
    assert ct.container_config({"container": {"name": "labpipe"}})["name"] == "labpipe"


def test_the_port_is_the_first_free_one_and_is_saved_in_the_projects_local_yaml(tmp_path, monkeypatch):
    project = _project_root(tmp_path, monkeypatch,
                            local="raw_roots: []\ncontainer:\n  engine: podman\n  popups: true\n")
    busy = []
    monkeypatch.setattr(ct, "free_port", lambda *a, **k: 8767)
    assert ct.ensure_port() is True
    block = ct.local_container_block()
    assert block["port"] == 8767 and block["engine"] == "podman" and block["popups"] is True
    assert "raw_roots" in (project / "config" / "local.yaml").read_text()
    monkeypatch.setattr(ct, "free_port", lambda *a, **k: busy.append(1) or 9000)
    assert ct.ensure_port() is False and busy == []            # already chosen: kept


def test_free_port_skips_ports_in_use():
    import socket
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        port = taken.getsockname()[1]
        assert ct.free_port(port, port + 50) != port


def test_forward_from_a_subfolder_of_the_project(tmp_path, monkeypatch):
    project = _project_root(tmp_path, monkeypatch,
                            local="container:\n  engine: podman\n")
    (project / "analysis" / "S-1").mkdir(parents=True)
    monkeypatch.chdir(project / "analysis" / "S-1")
    assert ct.safety.find_project() == project.resolve()
    monkeypatch.setattr(ct.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(ct, "running", lambda engine, name: True)
    calls = []
    monkeypatch.setattr(ct.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert ct.forward(["status"]) == 0
    command = calls[0]
    assert command[command.index("-w") + 1] == "/project/analysis/S-1"
    assert command[-2:] == ["labpipe", "status"] and "labpipe-pd-films" in command  # its container
