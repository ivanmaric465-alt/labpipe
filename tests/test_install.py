"""The installer and the slim host: what `labpipe setup --first` asks and writes, install.sh in a
scratch folder, and that the host-side commands work without numpy, scipy and matplotlib.
Nothing here touches the real home folder, the real labpipe link or an engine."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from labpipe import installer, project, safety
from labpipe.methods.xrd import refbuild

REPO = Path(__file__).resolve().parents[1]
HEAVY = ("numpy", "scipy", "matplotlib")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A home folder and a config folder of our own; nothing of the user's is read."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LABPIPE_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("LABPIPE_PROJECT", "LABPIPE_ROOT", "LABPIPE_CODE", "LABPIPE_HOME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _answers(tmp_path, **extra) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    body = {"engine": "podman", "harness": "claude-code", "popups": False,
            "project": {"name": "demo", "folder": str(tmp_path / "projects" / "demo"),
                        "data": [str(data)], "baseline": True}, **extra}
    path = tmp_path / "answers.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


# ---- setup --first ---------------------------------------------------------------------

def test_answers_file_writes_the_defaults_and_a_project_with_the_container_block(scratch, capsys):
    answers = _answers(scratch, popups=True, models={"postdoc": "sonnet"})
    assert installer.run(str(answers), start=False) == 0
    defaults = yaml.safe_load((scratch / "config" / "defaults.yaml").read_text(encoding="utf-8"))
    assert defaults["container"] == {"engine": "podman",
                                     "popups": sys.platform.startswith("linux")}
    assert defaults["agents"]["harness"] == "claude-code"
    assert defaults["agents"]["models"] == {"supervisor": "haiku", "analyst": "sonnet",
                                            "postdoc": "sonnet"}       # recommended, one changed
    folder = scratch / "projects" / "demo"
    local = yaml.safe_load((folder / "config" / "local.yaml").read_text(encoding="utf-8"))
    assert local["container"] == defaults["container"]
    assert local["agents"] == defaults["agents"]
    assert local["raw_roots"][0]["path"] == (scratch / "data").resolve().as_posix()
    assert (folder / "labpipe.yaml").is_file() and (folder / ".claude" / "agents").is_dir()
    out = capsys.readouterr().out
    assert f"Open Claude Code in {folder} and say: start" in out
    assert "--no-start" in out and "labpipe container start" in out


def test_codex_gets_reasoning_and_its_name_in_the_last_line(scratch, capsys):
    answers = _answers(scratch, harness="codex")
    assert installer.run(str(answers), start=False) == 0
    defaults = yaml.safe_load((scratch / "config" / "defaults.yaml").read_text(encoding="utf-8"))
    assert defaults["agents"]["reasoning"] == {"supervisor": "low", "analyst": "medium",
                                               "postdoc": "high"}
    assert "Open Codex in" in capsys.readouterr().out
    assert (scratch / "projects" / "demo" / ".codex" / "agents").is_dir()


def test_a_wrong_model_or_data_folder_stops_with_a_message(scratch, capsys):
    bad = _answers(scratch, models={"supervisor": "gpt-9"})
    assert installer.run(str(bad), start=False) == 1
    assert "anthropic/<model>" in capsys.readouterr().out
    missing = _answers(scratch)
    body = yaml.safe_load(missing.read_text(encoding="utf-8"))
    body["project"]["data"] = [str(scratch / "nowhere")]
    missing.write_text(yaml.safe_dump(body), encoding="utf-8")
    assert installer.run(str(missing), start=False) == 1
    assert "data folder not found" in capsys.readouterr().out
    assert not (scratch / "projects").exists()


def test_a_rerun_keeps_the_earlier_choices_as_the_defaults(scratch):
    answers = _answers(scratch, models={"analyst": "opus"})
    assert installer.run(str(answers), start=False) == 0
    again = scratch / "again.yaml"
    again.write_text(yaml.safe_dump({"project": {"name": "second", "folder": str(
        scratch / "projects" / "second")}}), encoding="utf-8")
    assert installer.run(str(again), start=False) == 0
    defaults = yaml.safe_load((scratch / "config" / "defaults.yaml").read_text(encoding="utf-8"))
    assert defaults["agents"]["models"]["analyst"] == "opus"
    assert (scratch / "projects" / "second" / "labpipe.yaml").is_file()


def test_project_new_without_defaults_has_no_container_block(scratch):
    folder = scratch / "plain"
    project.new(folder, [], "plain")
    local = yaml.safe_load((folder / "config" / "local.yaml").read_text(encoding="utf-8"))
    assert "container" not in local


def test_questions_are_asked_in_plain_words_and_enter_takes_the_defaults(scratch, monkeypatch,
                                                                         capsys):
    data = scratch / "mydata"
    data.mkdir()
    monkeypatch.setattr(installer, "prerequisites", lambda *a, **k: [])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    blanks = [""] * (4 + sys.platform.startswith("linux"))   # tool, 3 models, (pop-ups)
    replies = iter([*blanks, "proj", str(scratch / "p"), str(data), "", ""])
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        return next(replies)

    monkeypatch.setattr("builtins.input", fake_input)
    assert installer.run(None, start=False) == 0
    text = " ".join(asked)
    assert "Model for the supervisor [haiku]" in text and "Project name [my-project]" in text
    out = capsys.readouterr().out
    assert "supervisor:" in out and "analyst:" in out and "postdoc:" in out   # role descriptions
    assert (scratch / "p" / "labpipe.yaml").is_file()
    assert yaml.safe_load((scratch / "p" / "config" / "local.yaml").read_text(
        encoding="utf-8"))["raw_roots"][0]["path"] == data.resolve().as_posix()


def test_without_a_terminal_and_without_answers_it_says_so(scratch, capsys):
    assert installer.run(None) == 2
    assert "--answers" in capsys.readouterr().out


# ---- what is missing --------------------------------------------------------------------

@pytest.mark.parametrize("release, family", [
    ({"ID": "fedora"}, "fedora"), ({"ID": "ubuntu", "ID_LIKE": "debian"}, "debian"),
    ({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}, "debian"), ({"ID": "arch"}, "linux")])
def test_the_linux_family_comes_from_os_release(release, family):
    assert installer.os_family("Linux", release) == family
    assert installer.os_family("Darwin") == "macos" and installer.os_family("Windows") == "windows"


def test_missing_engine_and_tool_get_the_commands_for_this_system():
    nothing = lambda name: None
    for family, engine, tool in [("fedora", "sudo dnf install podman", "curl -fsSL https://claude.ai"),
                                 ("debian", "sudo apt install podman", "curl -fsSL https://claude.ai"),
                                 ("macos", "brew install podman", "curl -fsSL https://claude.ai"),
                                 ("windows", "winget install RedHat.Podman",
                                  "irm https://claude.ai/install.ps1")]:
        missing = installer.prerequisites("claude-code", family, which=nothing)
        text = json.dumps(missing)
        assert engine in text and tool in text, family
    codex = json.dumps(installer.prerequisites("codex", "debian", which=nothing))
    assert "npm install -g @openai/codex" in codex and "sudo apt install nodejs npm" in codex
    assert installer.prerequisites("claude-code", "fedora", which=lambda n: "/usr/bin/" + n) == []
    only_docker = installer.prerequisites("claude-code", "fedora",
                                          which=lambda n: None if n in ("podman", "claude") else "/x")
    assert [item["what"].split(",")[0] for item in only_docker] == ["Claude Code"]


def test_missing_tools_stop_the_installer_and_say_to_rerun_it(scratch, monkeypatch, capsys):
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    answers = _answers(scratch)
    assert installer.run(str(answers), start=True) == 3
    out = capsys.readouterr().out
    assert "Podman" in out and "Claude Code" in out and "Then rerun the installer" in out
    assert not (scratch / "config" / "defaults.yaml").exists()      # nothing half-done
    assert not (scratch / "projects").exists()
    # with --no-start the same machine goes on (a note instead of a stop)
    assert installer.run(str(answers), start=False) == 0
    assert "Skipped checks" in capsys.readouterr().out


# ---- the optional dependency message -----------------------------------------------------

def test_missing_pymatgen_names_this_python_and_the_pinned_version(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymatgen.analysis.diffraction.xrd", None)
    pin = re.search(r'"(pymatgen==[^"]+)"', (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    with pytest.raises(SystemExit) as error:
        refbuild.build_one({})
    message = str(error.value)
    assert f'"{sys.executable}" -m pip install "{pin.group(1)}"' in message
    assert "not in the container" in message
    assert safety.optional_install_command("pymatgen") in message


# ---- Python 3.10 and no heavy libraries on the host --------------------------------------

ANALYSIS = ("methods", "plotting")


def _host_sources() -> list[Path]:
    root = REPO / "src" / "labpipe"
    return [p for p in sorted(root.rglob("*.py")) if p.relative_to(root).parts[0] not in ANALYSIS]


def test_host_modules_use_nothing_newer_than_python_3_10():
    problems = []
    banned = re.compile(r"\b(datetime\.UTC|import UTC|from datetime import[^\n]*\bUTC\b|"
                        r"StrEnum|ExceptionGroup|TaskGroup|\.full_match\(|itertools\.batched|"
                        r"Path\.walk|\.walk\(top|add_note|except\*|typing import[^\n]*\b(Self|override)\b)")
    for path in _host_sources():
        text = path.read_text(encoding="utf-8")
        try:
            ast.parse(text, feature_version=(3, 10))
        except SyntaxError as error:
            problems.append(f"{path.name}: {error}")
        problems += [f"{path.name}: {m.group(0)}" for m in banned.finditer(text)]
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"\bf\"[^\"\n]*\{[^}\n]*\"", line) or re.search(r"\bf'[^'\n]*\{[^}\n]*'", line):
                problems.append(f"{path.name}:{number}: quote reused inside an f-string (3.12)")
        if re.search(r"^import tomllib|^from tomllib", text, re.M):
            problems.append(f"{path.name}: tomllib at module level (3.11)")
    assert problems == []


BLOCK = """
import sys, importlib.abc
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in %r:
            raise ImportError("blocked: " + name)
sys.meta_path.insert(0, _Block())
""" % (HEAVY,)


def _blocked(code: str, cwd: Path, env: dict, stdin: str | None = None
             ) -> subprocess.CompletedProcess:
    """Run `code` in a fresh Python where numpy, scipy and matplotlib cannot be imported."""
    script = BLOCK + "import json, io\nfrom labpipe import cli\n" + code + (
        "\nassert not [m for m in sys.modules if m.split('.')[0] in %r], 'a heavy module loaded'\n"
        % (HEAVY,))
    return subprocess.run([sys.executable, "-c", script], cwd=cwd, env=env, input=stdin,
                          capture_output=True, text=True, timeout=120)


def _env(scratch: Path, extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LABPIPE_")}
    env.update({"HOME": str(scratch / "home"), "USERPROFILE": str(scratch / "home"),
                "LABPIPE_CONFIG_HOME": str(scratch / "config"),
                "PYTHONPATH": str(REPO / "src"), "PYTHONUTF8": "1", **(extra or {})})
    return env


def test_host_commands_run_without_numpy_scipy_matplotlib(scratch, tmp_path):
    env = _env(scratch)
    data = scratch / "data"
    data.mkdir()
    folder = scratch / "work" / "hostproj"
    fake = scratch / "bin"
    fake.mkdir()
    engine = fake / "podman"          # an engine that says "running" and succeeds at everything
    engine.write_text("#!/bin/sh\necho '[{\"State\": {\"Running\": true}, \"Config\": {\"Labels\": {}}}]'\n",
                      encoding="utf-8")
    engine.chmod(0o755)
    (scratch / "config").mkdir()
    (scratch / "config" / "defaults.yaml").write_text(
        "container: {engine: podman, popups: false}\n", encoding="utf-8")
    first = _blocked(f"rc = cli.main(['project', 'new', {str(folder)!r}, '--data', {str(data)!r}])\n"
                     "assert rc == 0, rc", scratch, env)
    assert first.returncode == 0, first.stderr
    help_run = _blocked("""
try:
    cli.main(['--help'])
except SystemExit as stop:
    assert stop.code == 0
""", folder, env)
    assert help_run.returncode == 0, help_run.stderr
    event = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "README.md"},
                        "cwd": str(folder)})
    env_engine = {**env, "PATH": f"{fake}{os.pathsep}{env['PATH']}"}
    rest = _blocked(f"""
import contextlib
def run(argv, stdin=None):
    out = io.StringIO()
    old = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out):
            rc = cli.main(argv)
    finally:
        sys.stdin = old
    return rc, out.getvalue()
assert run(['project', 'sync'])[0] == 0
assert run(['agents', 'sync'])[0] == 0
rc, text = run(['doctor'])
assert 'python' in [c['check'] for c in json.loads(text)['checks']]
rc, text = run(['container', 'status'])
assert json.loads(text)['engine'] == 'podman'
rc, text = run(['guard'], stdin={event!r})
from labpipe import container
assert container.forward(['status']) == 0           # sent into the "running" container
assert container.forward(['xrd', 'refs', 'list']) is None   # run on the host
""", folder, env_engine)
    assert rest.returncode == 0, rest.stdout + rest.stderr


# ---- install.sh in a scratch folder --------------------------------------------------------

def _copy_app(destination: Path) -> None:
    shutil.copytree(REPO, destination, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.egg-info", ".pytest_cache", "analysis", "logs", "node_modules"))


bash = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


@bash
def test_install_sh_refuses_to_replace_a_foreign_labpipe_link(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "labpipe").symlink_to("/bin/true")
    env = _env(tmp_path, {"LABPIPE_HOME": str(tmp_path / "lp"), "LABPIPE_BIN": str(bin_dir),
                          "LABPIPE_NO_SETUP": "1"})
    run = subprocess.run(["bash", str(REPO / "install.sh")], env=env, capture_output=True,
                         text=True, timeout=120)
    assert run.returncode == 1
    assert "points elsewhere" in run.stderr
    assert (bin_dir / "labpipe").readlink() == Path("/bin/true")        # untouched
    assert not (tmp_path / "lp" / "venv").exists()                      # nothing installed


@bash
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("LABPIPE_TEST_OFFLINE") == "1", reason="needs the network (pip)")
def test_install_sh_with_answers_makes_a_slim_installation_and_a_project(tmp_path):
    home, bin_dir = tmp_path / "lp", tmp_path / "bin"
    _copy_app(home / "app")
    answers = _answers(tmp_path)
    env = _env(tmp_path, {"LABPIPE_HOME": str(home), "LABPIPE_BIN": str(bin_dir)})
    env.pop("PYTHONPATH")
    command = ["bash", str(home / "app" / "install.sh"), "--no-start", "--answers", str(answers)]
    for _ in range(2):                                   # rerunnable
        run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=900,
                             cwd=tmp_path)
        assert run.returncode == 0, run.stdout + run.stderr
    assert (home / "app" / ".labpipe-installed").is_file()
    assert (bin_dir / "labpipe").readlink() == home / "venv" / "bin" / "labpipe"
    python = home / "venv" / "bin" / "python"
    heavy = subprocess.run([str(python), "-c", "import numpy"], capture_output=True)
    assert heavy.returncode != 0, "the host install must not contain numpy"
    assert yaml.safe_load((tmp_path / "config" / "defaults.yaml").read_text(encoding="utf-8")
                          )["container"]["engine"] == "podman"
    folder = tmp_path / "projects" / "demo"
    assert (folder / "labpipe.yaml").is_file()
    # the installed command works in the project, and outside one it says how to make one
    doctor = subprocess.run([str(bin_dir / "labpipe"), "doctor"], cwd=folder, env=env,
                            capture_output=True, text=True)
    checks = {c["check"]: c for c in json.loads(doctor.stdout)["checks"]}
    assert checks["python"]["status"] == "ok" and "install" in checks and "defaults" in checks
    elsewhere = subprocess.run([str(bin_dir / "labpipe"), "status"], cwd=tmp_path, env=env,
                               capture_output=True, text=True)
    assert elsewhere.returncode == 2 and "labpipe project new" in elsewhere.stdout
