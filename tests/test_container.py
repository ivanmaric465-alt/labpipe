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


def test_container_hash_changes_with_container_files(tmp_path, monkeypatch):
    (tmp_path / "container").mkdir()
    (tmp_path / "container" / "Dockerfile").write_text("FROM x\n")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(ct.safety, "PROJECT_ROOT", tmp_path)
    first = ct.container_hash()
    assert len(first) == 12 and first == ct.container_hash()
    (tmp_path / "container" / "Dockerfile").write_text("FROM y\n")
    assert ct.container_hash() != first


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
                      "/labpipe/sub", "labpipe", "labpipe", "scan"]
    outside_cwd = _fwd(["status"], tmp_path.parent, tmp_path)
    assert "-t" not in outside_cwd and outside_cwd[outside_cwd.index("-w") + 1] == "/labpipe"
    cmd = _fwd(["replay", "m.yaml", f"--out={tmp_path}/a", "S-1"], tmp_path / "sub", tmp_path)
    assert cmd[-3:] == ["/labpipe/sub/m.yaml", "--out=/labpipe/a", "S-1"]


def test_native_watcher_refused_in_container_mode(tmp_path):
    for argv in (["automation", "watch"], ["automation", "setup"]):
        with pytest.raises(ct.ForwardError, match="the container runs the watcher"):
            _fwd(argv, tmp_path, tmp_path)
    assert _fwd(["automation", "run"], tmp_path, tmp_path)[-3:] == ["labpipe", "automation", "run"]


def test_forward_refuses_outside_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "elsewhere.json"   # absolute on every OS (Windows: with a drive)
    with pytest.raises(ct.ForwardError, match="outside the repository"):
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
