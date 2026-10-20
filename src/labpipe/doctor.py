"""`labpipe doctor`: where is this installation in its setup? (read-only, writes nothing)

Each check returns ok / todo / warn with a one-line `next` step. The agent's
first-session flow (CLAUDE.md) walks the user through the first non-ok item,
reruns doctor, and repeats. Doctor never writes anything.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from collections import Counter
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

from labpipe import autostart
from labpipe import manifest as mf
from labpipe.labmethods import MethodsError
from labpipe.registry import load_methods
from labpipe.container import container_mode
from labpipe import safety
from labpipe.safety import (CODE_ROOT, PROJECT_ROOT, SetupError, in_container,
                            load_pipeline_config, synced_marker)


def _item(check: str, status: str, message: str, next_step: str | None = None,
          **details: Any) -> dict[str, Any]:
    return {"check": check, "status": status, "message": message, "next": next_step, **details}


_synced = synced_marker


def _read_only(path: Path) -> tuple[bool | None, str]:
    """Is `path` read-only for this process? Never writes to find out."""
    if platform.system() == "Linux":
        best, options = "", ""
        try:
            with open("/proc/self/mounts", encoding="utf-8") as stream:
                for line in stream:
                    fields = line.split()
                    mount = fields[1].replace("\\040", " ")
                    if (str(path) == mount or str(path).startswith(mount.rstrip("/") + "/")) \
                            and len(mount) >= len(best):
                        best, options = mount, fields[3]
        except OSError:
            pass
        if "ro" in options.split(","):
            return True, f"mounted read-only ({best})"
    writable = os.access(path, os.W_OK)
    if writable:
        return False, "this account can write here"
    return (True if platform.system() != "Windows" else None,
            "not writable for this account" if platform.system() != "Windows"
            else "write permission not detected, but Windows ACLs cannot be fully checked "
                 "without writing; confirm the share/folder is read-only (docs/SAFETY.md)")


def _host_only() -> bool:
    """This environment only forwards into the container (no project yet, or container mode):
    it needs Python 3.10+ and PyYAML, not the analysis libraries."""
    return (safety.NO_PROJECT or container_mode()) and not in_container()


def _installation() -> list[dict[str, Any]]:
    """An installation (not a repository): where it is, the host Python, the container engine,
    whether the image is current, the user's defaults file."""
    from labpipe import container
    from labpipe.installer import engine_hint, os_family
    from labpipe.project import user_defaults_file
    items = [_item("install", "ok", f"installation {safety.install_home()}: code {CODE_ROOT}, "
                   f"venv {sys.prefix}")]
    engine = shutil.which("podman") or shutil.which("docker")
    if not engine:
        items.append(_item("engine", "warn", "neither Podman nor Docker is installed",
                           "the user runs: " + "; ".join(engine_hint(os_family()))))
    else:
        name = Path(engine).name.removesuffix(".exe")
        tag = container.image_tag({**container.DEFAULTS, **container.pipeline_container_block()})
        try:
            found = container.image_exists(name, tag)
        except OSError:
            found = False
        items.append(_item("engine", "ok", f"{container.engine_version(name) or name}; "
                           + (f"image {tag} is current" if found else
                              f"image for this code ({tag}) not built yet"),
                           None if found else "`labpipe container start` in a project builds it"))
    defaults = user_defaults_file()
    items.append(_item("defaults", "ok" if defaults.is_file() else "warn",
                       f"defaults for new projects: {defaults}" if defaults.is_file() else
                       f"no {defaults}: new projects get the built-in defaults",
                       None if defaults.is_file() else f"the user reruns the installer "
                       f"({CODE_ROOT / 'install.sh'}) or `labpipe setup --first`"))
    return items


def _environment() -> list[dict[str, Any]]:
    items = []
    version = ".".join(map(str, sys.version_info[:3]))
    host = _host_only()
    wanted = (3, 10) if host else (3, 14)
    ok_python = sys.version_info >= wanted
    items.append(_item("python", "ok" if ok_python else "todo",
                       f"Python {version} at {sys.executable}"
                       + (" (host: it only starts the container; the analysis runs there)"
                          if host else ""),
                       None if ok_python else
                       "install Python ≥ " + ".".join(map(str, wanted)) + " (README)"))
    on_path = shutil.which("labpipe")
    items.append(_item("labpipe_on_path", "ok" if on_path else "todo",
                       f"`labpipe` on PATH: {on_path}" if on_path else
                       "`labpipe` is not on PATH, so agents cannot run it as `labpipe ...`",
                       None if on_path else "activate the venv before starting the agent, or "
                       "link the venv's labpipe into a folder on PATH (README → Install)"))
    if safety.is_installation(CODE_ROOT):
        items += _installation()
    if _synced(Path(sys.prefix)):
        items.append(_item("venv_location", "warn", f"virtual environment {sys.prefix} is inside a "
                           "synced folder", "recreate it outside synced folders (README → Install)"))
    # inside the container the image's own lock applies (container/requirements.lock)
    lock = CODE_ROOT / ("container" if in_container() else "") / "requirements.lock"
    if lock.exists() and not host:
        mismatched = []
        for line in lock.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "==" not in line:
                continue
            name, wanted = (part.strip() for part in line.split("==", 1))
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                installed = None
            if installed != wanted and name.lower() not in ("pymatgen",):
                mismatched.append(f"{name} {installed or 'missing'} (lock: {wanted})")
        items.append(_item("dependencies", "warn" if mismatched else "ok",
                           "installed versions differ from requirements.lock: " + "; ".join(
                               mismatched[:8]) if mismatched else "matches requirements.lock",
                           "pip install -r requirements.lock" if mismatched else None))
    if _synced(PROJECT_ROOT):
        items.append(_item("project_location", "warn", f"project {PROJECT_ROOT} is inside a "
                           "synced folder; analysis/ and manifest.json should not be synced",
                           "create the project in a local, unsynced folder "
                           "(`labpipe project new <folder>`) and move your files there"))
    return items


def _project() -> list[dict[str, Any]]:
    """Which project this is, its mode, and whether the generated files are current."""
    from labpipe import project
    state = project.status()
    where = f"project {state['name']} ({state['path']})"
    if state["mode"] == "dev":
        return [_item("project", "ok", f"{where}: dev mode (the repository is its own project; "
                      "AGENTS.md, docs/ and .claude/ are the tracked sources)", mode="dev")]
    generated = state["generated"]
    if generated["current"]:
        return [_item("project", "ok", f"{where}: generated files current", mode="project")]
    detail = []
    if generated["missing"]:
        detail.append("missing: " + ", ".join(generated["missing"][:5]))
    if generated["edited"]:
        detail.append("edited by hand (sync overwrites): " + ", ".join(generated["edited"][:5]))
    if generated["sources_changed"]:
        detail.append("the code or lab/LAB.md changed since the last sync")
    return [_item("project", "todo", f"{where}: generated files out of date ("
                  + "; ".join(detail) + ")", "run `labpipe project sync`", mode="project")]


def _project_release() -> list[dict[str, Any]]:
    """The project was set up or updated with another labpipe release than the installed one
    (not blocking: `labpipe update` in the project brings it up; the user runs it)."""
    from labpipe import __version__, project
    if in_container() or safety.dev_mode():
        return []
    recorded = project.read_project().get("labpipe_version")
    if not recorded or str(recorded).lstrip("v") == __version__:
        return []
    return [_item("project_release", "warn",
                  f"this project was set up with labpipe {str(recorded).lstrip('v')}; the "
                  f"installation is {__version__}: run labpipe update here",
                  "show the user `labpipe update --check` (what changed); they run `labpipe "
                  "update` in their terminal (never you)")]


def _settings_migrations() -> list[dict[str, Any]]:
    """Settings files that still use names an update has changed (not blocking: old names keep
    working for one release)."""
    from labpipe import migrations
    try:
        pending = migrations.plan(PROJECT_ROOT)
    except Exception:       # doctor must not fail because of this check
        return []
    if not pending:
        return []
    return [_item("settings_migrations", "warn",
                  f"{len(pending)} settings file(s) use old names: run labpipe migrate "
                  f"({', '.join(item['file'] for item in pending[:4])})",
                  "show the user `labpipe migrate` (what it would change); apply it with "
                  "`labpipe migrate --apply` only with their OK", files=len(pending))]


def _onboarding(config: dict | None) -> list[dict[str, Any]]:
    """The first conversation is not finished (a warning, never blocking: `ready` counts `todo`
    only). Not in dev mode: the repository is its own project, nobody is onboarded there."""
    from labpipe import onboarding
    if config is None or safety.dev_mode():
        return []
    left = onboarding.remaining(config)
    if left is None:
        return []
    step, handled, total = left
    return [_item("onboarding", "warn", f"onboarding: next step {step} ({handled} of {total} done)",
                  f"follow {onboarding.doc_path()} → {step}; `labpipe onboarding done {step}` "
                  "after each step (the user can skip any step or all: `labpipe onboarding "
                  "skip all`)")]


def _container() -> list[dict[str, Any]]:
    """Container mode on this computer: running, up to date, logged in."""
    from labpipe.container import ContainerError, status_record
    try:
        state = status_record()
    except (ContainerError, SetupError) as exc:
        return [_item("container", "todo", str(exc), "labpipe container setup")]
    status, message, step = autostart.check(state["engine"])
    start = _item("autostart", status, message, step)
    if not state["running"]:
        return [_item("container", "todo", f"container {state['name']} is not running",
                      state["next"] or "labpipe container start"), start]
    items = [start, _item("container", "warn" if state["image_up_to_date"] is False else "ok",
                   f"container {state['name']} running ({state['engine']}); notifications page: "
                   f"{state['page']}" + ("; its image is older than container/"
                                          if state["image_up_to_date"] is False else ""),
                   "labpipe container start --replace" if state["image_up_to_date"] is False
                   else None)]
    harness = ((load_pipeline_config().get("agents") or {}).get("harness"))
    if harness == "claude-code" and not state["claude_login"]:
        items.append(_item("container_login", "todo", "the container has no Claude login, so "
                           "automatic AI runs stop at 'Not logged in'",
                           "the user runs `claude setup-token`, then `labpipe container login "
                           "claude` and `labpipe container start --replace`"))
    return items


def _raw_roots(config: dict) -> tuple[list[dict[str, Any]], list[tuple[str, Path]]]:
    items, usable = [], []
    if not config["raw_roots"]:
        items.append(_item("raw_roots", "todo", "no raw_roots in config/local.yaml",
                           "ask the user where the raw data is; set raw_roots (docs/SAFETY.md)"))
        return items, usable
    protected = [Path(os.path.realpath(os.path.expanduser(p))) for p in config["protected_paths"]]
    for root in config["raw_roots"]:
        path = Path(os.path.expanduser(root["path"]))
        label = f"raw_root:{root['id']}"
        if not path.is_dir():
            items.append(_item(label, "todo", f"{path} does not exist or is not mounted",
                               "set up the read-only view: labpipe readonly-view --source <raw folder>, or fix the path"))
            continue
        usable.append((root["id"], path))
        read_only, how = _read_only(path)
        real = Path(os.path.realpath(path))
        is_protected = any(real == p or p in real.parents for p in protected)
        if read_only is True:
            items.append(_item(label, "ok", f"{path}: {how}"))
        elif container_mode():
            items.append(_item(label, "ok", f"{path}: writable on this computer; the container "
                               "reads it read-only (container mode), and here the guard "
                               "keeps agents out of it when it is in protected_paths"))
        elif read_only is None:
            items.append(_item(label, "warn", f"{path}: {how}", "confirm with the user"))
        else:
            items.append(_item(label, "warn" if is_protected else "todo",
                               f"{path}: {how} (labpipe refuses to write there"
                               + (", it is in protected_paths)" if is_protected else
                                  ", but it is NOT in protected_paths)"),
                               "make it a read-only view: labpipe readonly-view --source <this folder>"
                               + ("" if is_protected else " and add it to protected_paths")))
        if not is_protected and read_only is not True:
            items.append(_item(f"protected:{root['id']}", "todo",
                               f"{path} is not listed in protected_paths",
                               "add it (and the writable original it mirrors) to protected_paths"))
    if container_mode():
        items.extend(_container())
    if in_container():
        items.append(_item("container", "ok", "inside the container: the raw data is the "
                           "read-only mount, and Claude Code's and Codex's own sandboxes are not "
                           "used here"))
    if os.environ.get("LABPIPE_RAW_ROOT"):
        items.append(_item("raw_root_override", "warn", "LABPIPE_RAW_ROOT is set and replaces "
                           "raw_roots for this run", "unset it for normal use"))
    return items, usable


def _inventory(config: dict) -> dict[str, Any]:
    """What is recognised in the data folders. On a host that only forwards into the container,
    the container lists it: recognising a file imports the method's code, which needs the
    analysis libraries that only the image has."""
    if _host_only():
        from labpipe.container import forward_json
        return forward_json(["inventory"])
    from labpipe.inventory import inventory
    return inventory(config)


def _files(config: dict, usable: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    from labpipe.container import ContainerError
    items = []
    usable_ids = {root_id for root_id, _ in usable}
    try:
        listed = _inventory(config)
    except ContainerError as exc:
        return [_item("files", "warn", f"the files are listed by the container: {exc}",
                      "labpipe container start, then labpipe doctor again")]
    for root in listed["roots"]:
        if root["id"] not in usable_ids:
            continue
        folders = (f"add new files to {root['data_folder']}; " if root["data_folder"] else "") \
            + f"labpipe reads them from {root['reads_from']} (read-only)"
        items.append(_item(f"data_folder:{root['id']}", "ok", folders))
        analysable = sum(n for info in root["recognised"].values()
                         for kind, n in info["files"].items() if kind.endswith("(analysed)"))
        unknown = {ext: info["files"] for ext, info in root["not_recognised"].items()}
        items.append(_item(f"files:{root['id']}", "ok" if analysable else "todo",
                           f"{analysable} file(s) a method can analyse; recognised: "
                           + ", ".join(f"{m} {info['files']}" for m, info in root["recognised"].items())
                           + (f"; not recognised: {unknown}" if unknown else ""),
                           ("show the user `labpipe inventory` and ask what the unrecognised "
                            "files are (AGENTS.md → Recognising the user's files)" if unknown
                            else None)
                           if analysable else "show the user `labpipe inventory`; ask what their "
                           "files are (AGENTS.md → Recognising the user's files)"))
        unclear = root["no_sample_id"]
        if analysable:
            items.append(_item(f"sample_ids:{root['id']}", "warn" if unclear["files"] else "ok",
                               f"{unclear['files']} of {analysable} file name(s) give no sample ID "
                               f"with pattern {unclear['pattern']}",
                               "show the user these names; adapt sample_id.pattern in "
                               "config/pipeline.yaml or add overrides in config/sample_ids.yaml"
                               if unclear["files"] else None, examples=unclear["examples"]))
    return items


def _old_automation() -> list[dict[str, Any]]:
    """The native systemd units of earlier versions: retired, the container replaces them."""
    units = autostart.old_units()
    if not units:
        return []
    return [_item("old_automation", "warn",
                  "old native automation is still installed (" + ", ".join(p.name for p in units)
                  + "); the container does this work now, and both together analyse twice",
                  "the user runs `labpipe automation setup`, or: "
                  + "; ".join(autostart.removal_commands(units)))]


def _automation(config: dict) -> list[dict[str, Any]]:
    """Is automatic processing (if set up) running, and did its last run succeed?"""
    if in_container():
        from labpipe.automation import health
        report = health(config)
        return [_item("automation", report["status"], report["message"], report["next"],
                      **report["details"])]
    if not container_mode():
        return [_item("automation", "ok", "no automatic processing: it runs in the project's "
                      "container (container mode: `labpipe container setup`); by hand: "
                      "`labpipe automation run`")]
    from labpipe.automation import health
    report = health(config)
    return [_item("automation", report["status"], report["message"], report["next"],
                  **report["details"])]


def _method_setup() -> list[dict[str, Any]]:
    items = []
    roles = {p.stem for p in (CODE_ROOT / "agents").glob("*.md")}
    roles |= {p.stem for p in (PROJECT_ROOT / "lab" / "agents").glob("*.md")}   # the lab's roles
    for name, spec in load_methods().items():
        agent = spec.get("subagent")
        if agent and agent not in roles:
            where = f"lab/agents/{agent}.md" if spec.get("lab") else f"agents/{agent}.md"
            items.append(_item(f"agent:{agent}", "todo", f"{'lab/methods.yaml' if spec.get('lab') else 'methods.yaml'}"
                               f" names agent {agent}, but {where} does not exist",
                               "see docs/ADDING_A_METHOD.md"))
    catalogue = CODE_ROOT / "references" / "xrd" / "phases.yaml"
    if catalogue.exists():
        phases = (yaml.safe_load(catalogue.read_text(encoding="utf-8")) or {}).get("phases") or []
        patterns = CODE_ROOT / "references" / "xrd" / "patterns"
        unbuilt = [p["id"] for p in phases if not (patterns / f"{p['id']}.json").exists()]
        items.append(_item("xrd_references", "todo" if unbuilt else "ok",
                           f"{len(phases)} reference phase(s)" + (
                               f"; not built: {', '.join(unbuilt)}" if unbuilt else ""),
                           ("the user runs `labpipe xrd refs build` on this computer; it needs pymatgen, and the "
                            "command reports the exact install command if it is missing")
                           if unbuilt else None))
    xrd = CODE_ROOT / "config" / "xrd.yaml"
    if xrd.exists():
        standard = (yaml.safe_load(xrd.read_text(encoding="utf-8")) or {}).get(
            "internal_standard") or {}
        if standard.get("phase") and standard.get("lattice_a_angstrom") is None:
            items.append(_item("xrd_internal_standard", "warn",
                               f"internal standard {standard['phase']}: no certificate lattice "
                               "parameter set; the COD value is used and flagged for review",
                               "ask the user for the certificate value "
                               "(internal_standard.lattice_a_angstrom in config/xrd.yaml)"))
    return items


def run() -> dict[str, Any]:
    if safety.NO_PROJECT:
        message = safety.NO_PROJECT_MESSAGE
        return {"project_root": None, "platform": platform.platform(), "ready": False,
                "next": {"check": "project", "do": "labpipe project new <folder>"},
                "checks": [*_environment(), _item("project", "todo", message,
                                                  "labpipe project new <folder>")]}
    items = _environment()
    try:
        load_methods()
    except MethodsError as exc:      # one clear item instead of a traceback in every command
        items.append(_item("lab_methods", "todo", str(exc), "fix lab/methods.yaml (see "
                           "docs/ADDING_A_METHOD.md); nothing else can run until then"))
        return {"project_root": str(PROJECT_ROOT), "platform": platform.platform(),
                "ready": False, "next": {"check": "lab_methods", "do": items[-1]["next"]},
                "checks": items}
    items += _project()
    items += _project_release()
    items += _settings_migrations()
    local = PROJECT_ROOT / "config" / "local.yaml"
    try:
        config = load_pipeline_config()
    except SetupError:
        items.append(_item("local_config", "todo", "config/local.yaml missing",
                           "labpipe init, then set raw_roots and protected_paths with the user"))
        config = None
    if config is not None:
        items.append(_item("local_config", "ok", f"{local.relative_to(PROJECT_ROOT)} present"))
        root_items, usable = _raw_roots(config)
        items += root_items
        items += _files(config, usable)
    items += _method_setup()
    if config is not None:
        from labpipe.agents import status as agents_status
        agents = agents_status(config)
        if not agents["configured"]:
            items.append(_item("agents", "todo", f"agents not set up: {agents['problem']}",
                               "ask the user which harness and which model per role "
                               "(supervisor, analyst); add `agents:` to config/local.yaml "
                               "(see local.example.yaml); then labpipe agents sync"))
        elif agents["outdated"]:
            items.append(_item("agents", "todo", f"{agents['harness']} agent files missing or "
                               f"outdated: {', '.join(agents['outdated'])}", "labpipe agents sync"))
        else:
            items.append(_item("agents", "ok", f"{agents['harness']} agents current; models: "
                               + ", ".join(f"{k} {v}" for k, v in agents["models"].items())))
        for problem in agents.get("problems") or []:
            items.append(_item("lab_agents", "warn", problem, "fix the file in lab/agents/, "
                               "then `labpipe project sync`"))
        if agents.get("harness") == "codex":
            from labpipe.agents import codex_trust
            if not shutil.which("codex"):
                items.append(_item("codex", "todo", "`codex` is not on PATH",
                                   "install the Codex CLI (README → Requirements)"))
            elif codex_trust() != "trusted":
                items.append(_item("codex", "todo", f"Codex does not trust {PROJECT_ROOT} yet: "
                                   "until then it ignores the project's .codex/ (rules, guard "
                                   "hook, agents)", "run `labpipe container login codex`" if in_container()
                                   else "the user opens the folder once in Codex "
                                   "(`codex` there) and chooses to trust it"))
            else:
                items.append(_item("codex", "ok", "codex installed; project trusted (rules, "
                                   "guard hook and agents in .codex/ apply)"))
    if not in_container():
        items += _old_automation()
    if config is not None and (PROJECT_ROOT / config["manifest"]).exists():
        counts = Counter(e["status"] for e in mf.load(config)["entries"].values())
        items.append(_item("manifest", "ok", f"manifest entries by status: {dict(counts)}"))
        items += _automation(config)
    elif config is not None:
        items.append(_item("manifest", "todo", "nothing scanned yet",
                           "labpipe scan, then analyse one file with the user"))
    items += _onboarding(config)          # last: setup items come first
    order = {"todo": 0, "warn": 1, "ok": 2}
    first = min((i for i in items if i["status"] != "ok"),
                key=lambda i: order[i["status"]], default=None)
    return {"project_root": str(PROJECT_ROOT), "platform": platform.platform(),
            "ready": not any(i["status"] == "todo" for i in items),
            "next": first and {"check": first["check"], "do": first["next"]},
            "checks": items}
