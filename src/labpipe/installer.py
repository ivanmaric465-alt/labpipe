"""`labpipe setup --first`: what the installer asks (docs/design/install.md → Install).

install.sh / install.ps1 make the environment, then call this. In plain words, with the default
in brackets (Enter takes it):

1. checks: Podman or Docker, and the agent tool (Claude Code or Codex) are installed; if not,
   the exact install commands for this system are shown (never run: some need admin rights)
   and the installer stops: rerun it afterwards;
2. which agent tool, which model per role, Linux desktop pop-ups;
3. writes defaults.yaml in the user's labpipe config folder (the base of every new project);
4. the login: shows the one command, stores the token as the container's secret;
5. the first project (`labpipe project new`), then the container (build, start), pop-ups,
   and the first scan if only new files are to be analysed;
6. ends with the notifications page and what to say to the agent.

`--answers FILE.yaml` answers every question from a file (keys below; CI and tests); a missing
key takes the default. `--no-start` skips the checks that need the tools, the login, the
build and the start. Only the host runs this: PyYAML and the standard library.

    engine: podman                     # podman | docker (default: podman if installed)
    harness: claude-code               # claude-code | codex
    models: {supervisor: haiku, analyst: sonnet, postdoc: opus}
    reasoning: {supervisor: low, ...}  # Codex only
    popups: false                      # Linux only
    project: {name: my-project, folder: ~/labpipe-projects/my-project,
              data: [~/labdata], baseline: true}
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from labpipe import agents, container, project
from labpipe.safety import CODE_ROOT, config_home, install_home

HARNESSES = {"claude-code": "Claude Code", "codex": "Codex"}
TOOL = container.HARNESS_PROGRAM                      # harness → program on PATH
ENGINE_NAMES = {"podman": "Podman", "docker": "Docker"}
DEFAULT_NAME = "my-project"


class InstallError(RuntimeError):
    pass


# ---- what is missing, and the commands that fix it ---------------------------------------

def os_family(system: str | None = None, release: dict[str, str] | None = None) -> str:
    """fedora | debian | linux | macos | windows (Ubuntu counts as debian)."""
    system = system or platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    if release is None:
        release = {}
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                release[key] = value.strip().strip('"')
        except OSError:
            pass
    ids = {release.get("ID", "")} | set(release.get("ID_LIKE", "").split())
    if ids & {"fedora", "rhel", "centos"}:
        return "fedora"
    if ids & {"debian", "ubuntu"}:
        return "debian"
    return "linux"


def engine_hint(family: str) -> list[str]:
    commands = {
        "fedora": ["sudo dnf install podman"],
        "debian": ["sudo apt install podman"],
        "macos": ["brew install podman", "podman machine init", "podman machine start"],
        "windows": ["winget install RedHat.Podman", "podman machine init",
                    "podman machine start"],
    }.get(family, ["see https://podman.io/docs/installation"])
    return commands


def tool_hint(harness: str, family: str) -> list[str]:
    if harness == "claude-code":
        return (["irm https://claude.ai/install.ps1 | iex"] if family == "windows"
                else ["curl -fsSL https://claude.ai/install.sh | bash"])
    node = {"fedora": "sudo dnf install nodejs npm", "debian": "sudo apt install nodejs npm",
            "macos": "brew install node", "windows": "winget install OpenJS.NodeJS"}.get(family)
    return [*([node + "        # if you have no Node.js yet"] if node else []),
            "npm install -g @openai/codex"]


def rerun_command() -> str:
    script = "install.ps1" if os.name == "nt" else "install.sh"
    return str(CODE_ROOT / script)


def prerequisites(harness: str, family: str, which=None) -> list[dict[str, Any]]:
    """What is missing: [{what, commands}] (empty: nothing)."""
    which = which or shutil.which
    missing = []
    if not (which("podman") or which("docker")):
        missing.append({"what": "Podman (or Docker), which runs the analysis in a container",
                        "commands": engine_hint(family)})
    if not which(TOOL[harness]):
        missing.append({"what": f"{HARNESSES[harness]}, the agent tool",
                        "commands": tool_hint(harness, family)})
    return missing


def show_missing(missing: list[dict[str, Any]]) -> None:
    print("\nSomething is missing. labpipe never runs installs for you; you run these "
          "(some need admin rights):")
    for item in missing:
        print(f"\n  {item['what']}:")
        for command in item["commands"]:
            print(f"      {command}")
    print(f"\nThen rerun the installer: {rerun_command()}")


# ---- the questions -----------------------------------------------------------------------

class Questions:
    """Answers from a file, or asked in the terminal (Enter takes the default)."""

    def __init__(self, answers: dict[str, Any] | None):
        self.answers = answers

    @property
    def interactive(self) -> bool:
        return self.answers is None

    def ask(self, key: str, question: str, default: str = "", section: str | None = None) -> str:
        """`key` (inside `section`, a mapping, if given) from the answers file, else asked."""
        if self.answers is not None:
            where = self.answers.get(section) if section else self.answers
            value = where.get(key) if isinstance(where, dict) else None
            return str(value) if value is not None else default
        answer = input(f"{question} [{default}]: " if default else f"{question}: ").strip()
        return answer or default

    def yes(self, key: str, question: str, default: bool) -> bool:
        if self.answers is not None:
            value = self.answers.get(key)
            return default if value is None else bool(value)
        text = input(f"{question} (y/n) [{'y' if default else 'n'}]: ").strip().lower()
        return default if not text else text.startswith("y")


def load_answers(path: str | Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise InstallError(f"cannot read the answers file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path}: the answers file must be a YAML mapping")
    return data


def _previous() -> dict[str, Any]:
    """The defaults of an earlier run (so a rerun offers the earlier choices)."""
    return project.user_defaults()


def choose_harness(q: Questions, previous: dict[str, Any]) -> str:
    current = (previous.get("agents") or {}).get("harness")
    default = current if current in HARNESSES else "claude-code"
    if q.interactive:
        print("\nWhich agent tool do you work with?")
        for number, name in enumerate(HARNESSES.values(), 1):
            print(f"  {number}  {name}")
    answer = q.ask("harness", "Choice", str(list(HARNESSES).index(default) + 1)
                   if q.interactive else default)
    harness = list(HARNESSES)[int(answer) - 1] if answer in ("1", "2") and q.interactive \
        else answer
    if harness not in HARNESSES:
        raise InstallError(f"harness must be one of {', '.join(HARNESSES)}, not {harness!r}")
    return harness


def choose_models(q: Questions, harness: str, previous: dict[str, Any]
                  ) -> tuple[dict[str, str], dict[str, str]]:
    """(models, reasoning) per role: the earlier choice or the recommendation, unless said."""
    from labpipe import wizard
    advice = wizard.recommended(harness)
    before = previous.get("agents") or {}
    same = before.get("harness") == harness
    if q.interactive:
        print("\nAgents run labpipe commands and write notes; they never calculate numbers. "
              "Three roles, each with its own model (Enter takes the recommendation):")
        for role in wizard.ROLE_ORDER:
            print(f"  {role}: {wizard.ROLE_TEXT[role]}")
    models: dict[str, str] = {}
    reasoning: dict[str, str] = {}
    for role in wizard.ROLE_ORDER:
        wish = advice.get(role) or {}
        old_model = (before.get("models") or {}).get(role) if same else None
        models[role] = q.ask(role, f"Model for the {role}",
                             str(old_model or wish.get("model") or "inherit"), "models")
        if harness == "codex":
            old_effort = (before.get("reasoning") or {}).get(role) if same else None
            reasoning[role] = q.ask(role, f"Reasoning effort for the {role} (low, medium, high, "
                                    "xhigh)", str(old_effort or wish.get("reasoning") or "medium"),
                                    "reasoning")
    return models, reasoning


def validate_agents(harness: str, models: dict[str, str], reasoning: dict[str, str]) -> None:
    """The same check `agents sync` makes: every role renders with these models."""
    block: dict[str, Any] = {"harness": harness, "models": models}
    if reasoning:
        block["reasoning"] = reasoning
    try:
        chosen = agents.settings({"agents": block})
        for role in agents.load_roles():
            agents._render(role, harness, models, chosen["reasoning"])
    except agents.AgentConfigError as exc:
        raise InstallError(f"models: {exc}") from exc


def defaults_text(engine: str, harness: str, models: dict[str, str],
                  reasoning: dict[str, str], popups: bool) -> str:
    block: dict[str, Any] = {"harness": harness, "models": models}
    if reasoning:
        block["reasoning"] = reasoning
    return ("# labpipe's defaults for new projects (written by the installer; edit freely).\n"
            "# `labpipe project new` copies them into the new project's config/local.yaml.\n"
            + yaml.safe_dump({"agents": block, "container": {"engine": engine, "popups": popups}},
                             sort_keys=False, allow_unicode=True, width=1000))


# ---- the steps ---------------------------------------------------------------------------

def _labpipe(args: list[str], cwd: Path, capture: bool = False) -> subprocess.CompletedProcess:
    """Run `labpipe …` in a project folder (a project is found from the folder it runs in)."""
    return subprocess.run([sys.executable, "-m", "labpipe.cli", *args], cwd=cwd, text=True,
                          stdout=subprocess.PIPE if capture else None)


def login_claude(engine: str, interactive: bool) -> str:
    """Show the one command; store the pasted token as the container's secret (not echoed)."""
    if container.claude_login_present(engine):
        print("A Claude login for the container is already stored; keeping it "
              "(`labpipe container login claude` replaces it).")
        return "kept"
    if not interactive:
        print("Login skipped (no terminal): later run `claude setup-token`, then "
              "`labpipe container login claude`.")
        return "skipped"
    print("\nLogin. In another terminal, run this and copy the token it prints:\n"
          "      claude setup-token\n"
          "Then paste it here (nothing is shown as you type). Enter alone skips this; "
          "`labpipe container login claude` does it later.")
    try:
        token = container.read_token()
    except container.ContainerError:
        print("Skipped.")
        return "skipped"
    print(f"Stored: {container.store_claude_token(engine, token)}")
    return "stored"


def ask_project(q: Questions) -> dict[str, Any]:
    if q.interactive:
        print("\nYour first project: one folder with its own data, settings and results.")
    name = q.ask("name", "Project name", DEFAULT_NAME, "project")
    folder = q.ask("folder", "Project folder (not in a synced folder like Dropbox or OneDrive)",
                   f"~/labpipe-projects/{project.slug(name)}", "project")
    data: list[str] = []
    baseline = True
    if q.interactive:
        print("Data folder: where your instrument files are. labpipe only reads it.")
        while True:
            answer = input("Data folder (Enter = " + ("no more" if data else "add one later")
                           + "): ").strip()
            if not answer:
                break
            if not Path(answer).expanduser().is_dir():
                print(f"  {answer} is not a folder that exists; try again.")
                continue
            data.append(answer)
        if data:
            print("Analyse what is already in the data folder, or only files added from now on?\n"
                  "  1  analyse everything already there (the analysis is code and free; "
                  "AI notes follow in batches)\n"
                  "  2  only new files (the existing ones are recorded and left alone; "
                  "`labpipe requeue --baseline` queues them later)")
            baseline = input("Choice [2]: ").strip() != "1"
    else:
        given = (q.answers or {}).get("project")
        given = given if isinstance(given, dict) else {}
        data = [str(item) for item in given.get("data") or []]
        baseline = bool(given.get("baseline", True))
    for item in data:
        if not Path(item).expanduser().is_dir():
            raise InstallError(f"data folder not found: {item}")
    return {"name": name, "folder": Path(folder).expanduser(), "data": data,
            "baseline": baseline}


def run(answers_file: str | None = None, start: bool = True) -> int:
    """The first-run questions and steps. Returns the exit code (0: done)."""
    interactive = answers_file is None
    if interactive and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("labpipe setup --first asks questions: run it in a terminal, or give the "
              "answers with --answers FILE.yaml.")
        return 2
    q = Questions(None if interactive else load_answers(answers_file))
    previous = _previous()
    family = os_family()
    try:
        return _run(q, previous, family, start, interactive)
    except InstallError as exc:
        print(f"\nERROR: {exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Nothing is lost: rerun the installer to continue.")
        return 1


def _run(q: Questions, previous: dict[str, Any], family: str, start: bool, interactive: bool
         ) -> int:
    print("labpipe: a few questions (Enter takes the answer in brackets). "
          "Nothing here needs admin rights.")
    harness = choose_harness(q, previous)
    # 1. what must be installed first
    missing = prerequisites(harness, family)
    if missing and start:
        show_missing(missing)
        return 3
    if missing:
        print("Not installed (needed to start the container and for the agent): "
              + "; ".join(item["what"] for item in missing) + ". Skipped checks (--no-start).")
    engine = shutil.which("podman") and "podman" or shutil.which("docker") and "docker" or "podman"
    engine = q.ask("engine", "", engine) if not interactive else engine
    if engine not in ENGINE_NAMES:
        raise InstallError(f"engine must be podman or docker, not {engine!r}")
    # 2. models, pop-ups
    models, reasoning = choose_models(q, harness, previous)
    validate_agents(harness, models, reasoning)
    popups = False
    if sys.platform.startswith("linux"):
        popups = q.yes("popups", "\nDesktop pop-ups when something needs you (a small user "
                       "service, installed only after you confirm it)",
                       bool((previous.get("container") or {}).get("popups")))
    # 3. defaults
    defaults = config_home() / "defaults.yaml"
    defaults.parent.mkdir(parents=True, exist_ok=True)
    defaults.write_text(defaults_text(engine, harness, models, reasoning, popups),
                        encoding="utf-8")
    print(f"\nSaved your choices: {defaults}")
    # 4. login
    if harness == "claude-code" and start:
        login_claude(engine, interactive)
    # 5. the first project
    info = ask_project(q)
    folder: Path = info["folder"]
    try:
        created = project.new(folder, info["data"], info["name"])
    except (project.ProjectError, OSError) as exc:
        raise InstallError(str(exc)) from exc
    print(f"\nProject created: {created['project']}")
    page = None
    if start:
        page = _start(folder, harness, popups, info, interactive)
    else:
        print("Not started (--no-start). Later, in the project folder:\n"
              f"      cd {folder}\n      labpipe container start")
    # 6. done
    print("\nDone.")
    if page:
        print(f"Notifications page: {page}")
    print(f"Open {HARNESSES[harness]} in {folder} and say: start")
    return 0


def _start(folder: Path, harness: str, popups: bool, info: dict[str, Any],
           interactive: bool) -> str | None:
    """Pop-ups, build and start the container, the first scan, the Codex login."""
    if popups and sys.platform.startswith("linux"):
        if interactive:
            _labpipe(["container", "popups", "on"], folder)
        else:
            print("Pop-ups: run `labpipe container popups on` in the project folder "
                  "(it asks before installing the service).")
    print("\nBuilding and starting the container (the first build takes a few minutes) ...")
    result = _labpipe(["container", "start"], folder, capture=True)
    page = None
    try:
        page = json.loads(result.stdout or "{}").get("page")
    except ValueError:
        pass
    if result.returncode != 0:
        print(result.stdout or "")
        raise InstallError(f"the container did not start. Fix the error above, then rerun the "
                           f"installer (or `cd {folder} && labpipe container start`)")
    if info["data"] and info["baseline"]:
        print("Recording the files already there (not analysed) ...")
        if _labpipe(["scan", "--baseline"], folder, capture=True).returncode != 0:
            print(f"The first scan failed: later run `cd {folder} && labpipe scan --baseline`.")
    if harness == "codex":
        if interactive:
            print("\nCodex login (it shows a code to enter in your browser):")
            _labpipe(["container", "login", "codex"], folder)
        else:
            print("Codex login: run `labpipe container login codex` in the project folder.")
    return page
