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

from labpipe import manifest as mf
from labpipe.registry import load_methods
from labpipe.container import container_mode
from labpipe.safety import PROJECT_ROOT, SetupError, in_container, load_pipeline_config

SYNC_MARKERS = ("koofr", "onedrive", "dropbox", "google drive", "googledrive", "icloud",
                "nextcloud", "owncloud", "syncthing", "mega")


def _item(check: str, status: str, message: str, next_step: str | None = None,
          **details: Any) -> dict[str, Any]:
    return {"check": check, "status": status, "message": message, "next": next_step, **details}


def _synced(path: Path) -> str | None:
    """A path segment named like a sync client's folder (e.g. 'OneDrive - Lab', 'Dropbox')."""
    for part in (p.lower() for p in Path(path).parts):
        for marker in SYNC_MARKERS:
            if part.startswith(marker) and (marker != "mega" or part in ("mega", "megasync")):
                return marker
    return None


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


def _environment() -> list[dict[str, Any]]:
    items = []
    version = ".".join(map(str, sys.version_info[:3]))
    items.append(_item("python", "ok" if sys.version_info >= (3, 14) else "todo",
                       f"Python {version} at {sys.executable}",
                       None if sys.version_info >= (3, 14) else "install Python ≥ 3.14 (README)"))
    on_path = shutil.which("labpipe")
    items.append(_item("labpipe_on_path", "ok" if on_path else "todo",
                       f"`labpipe` on PATH: {on_path}" if on_path else
                       "`labpipe` is not on PATH, so agents cannot run it as `labpipe ...`",
                       None if on_path else "activate the venv before starting the agent, or "
                       "link the venv's labpipe into a folder on PATH (README → Install)"))
    if _synced(Path(sys.prefix)):
        items.append(_item("venv_location", "warn", f"virtual environment {sys.prefix} is inside a "
                           "synced folder", "recreate it outside synced folders (README → Install)"))
    # inside the container the image's own lock applies (container/requirements.lock)
    lock = PROJECT_ROOT / ("container" if in_container() else "") / "requirements.lock"
    if lock.exists():
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
        items.append(_item("project_location", "warn", f"repository {PROJECT_ROOT} is inside a "
                           "synced folder; analysis/ and manifest.json should not be synced",
                           "clone the repository to a local, unsynced folder"))
    return items


def _container() -> list[dict[str, Any]]:
    """Container mode on this computer: running, up to date, logged in."""
    from labpipe.container import ContainerError, status_record
    try:
        state = status_record()
    except (ContainerError, SetupError) as exc:
        return [_item("container", "todo", str(exc), "labpipe container setup")]
    if not state["running"]:
        return [_item("container", "todo", f"container {state['name']} is not running",
                      state["next"] or "labpipe container start")]
    items = [_item("container", "warn" if state["image_up_to_date"] is False else "ok",
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
                           "read-only mount, Claude Code's and Codex's own sandboxes and the "
                           "systemd services are not used here"))
    if os.environ.get("LABPIPE_RAW_ROOT"):
        items.append(_item("raw_root_override", "warn", "LABPIPE_RAW_ROOT is set and replaces "
                           "raw_roots for this run", "unset it for normal use"))
    return items, usable


def _files(config: dict, usable: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    from labpipe.inventory import inventory
    items = []
    usable_ids = {root_id for root_id, _ in usable}
    for root in inventory(config)["roots"]:
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


def _automation(config: dict) -> list[dict[str, Any]]:
    """Is automatic processing (if set up) running, and did its last run succeed?"""
    if in_container():
        from labpipe.automation import health
        report = health(config)
        return [_item("automation", report["status"], report["message"], report["next"],
                      **report["details"])]
    if not config.get("automation"):
        return [_item("automation", "ok", "automatic processing not set up (optional: "
                      "`labpipe automation setup` in your terminal)")]
    from labpipe.automation import health
    report = health(config)
    return [_item("automation", report["status"], report["message"], report["next"],
                  **report["details"])]


def _method_setup() -> list[dict[str, Any]]:
    items = []
    roles = {p.stem for p in (PROJECT_ROOT / "agents").glob("*.md")}
    for name in [spec.get("subagent") for spec in load_methods().values()]:
        if name and name not in roles:
            items.append(_item(f"agent:{name}", "todo", f"methods.yaml names agent {name}, but "
                               f"agents/{name}.md does not exist", "see docs/ADDING_A_METHOD.md"))
    catalogue = PROJECT_ROOT / "references" / "xrd" / "phases.yaml"
    if catalogue.exists():
        phases = (yaml.safe_load(catalogue.read_text(encoding="utf-8")) or {}).get("phases") or []
        patterns = PROJECT_ROOT / "references" / "xrd" / "patterns"
        unbuilt = [p["id"] for p in phases if not (patterns / f"{p['id']}.json").exists()]
        items.append(_item("xrd_references", "todo" if unbuilt else "ok",
                           f"{len(phases)} reference phase(s)" + (
                               f"; not built: {', '.join(unbuilt)}" if unbuilt else ""),
                           "labpipe xrd refs build (needs the refbuild extra)" if unbuilt else None))
    xrd = PROJECT_ROOT / "config" / "xrd.yaml"
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
    items = _environment()
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
    if config is not None and (PROJECT_ROOT / config["manifest"]).exists():
        counts = Counter(e["status"] for e in mf.load(config)["entries"].values())
        items.append(_item("manifest", "ok", f"manifest entries by status: {dict(counts)}"))
        items += _automation(config)
    elif config is not None:
        items.append(_item("manifest", "todo", "nothing scanned yet",
                           "labpipe scan, then analyse one file with the user"))
    order = {"todo": 0, "warn": 1, "ok": 2}
    first = min((i for i in items if i["status"] != "ok"),
                key=lambda i: order[i["status"]], default=None)
    return {"project_root": str(PROJECT_ROOT), "platform": platform.platform(),
            "ready": not any(i["status"] == "todo" for i in items),
            "next": first and {"check": first["check"], "do": first["next"]},
            "checks": items}
