"""`labpipe setup`: guided setup in the user's own terminal (the installers offer it).

Four steps, each optional and safe to rerun; current settings are the defaults:

1. raw-data: where the instrument files are, and a folder labpipe can only read.
   Commands that need admin rights or change permissions on the raw data are
   printed for the user to run; this never runs them. It then checks the result
   and writes `raw_roots` / `protected_paths` into config/local.yaml.
2. sandbox: are the prerequisites of the Claude Code sandbox present (needed
   for unattended runs)?
3. agents: harness and a model per role → `agents:`, then `labpipe agents sync`.
4. automation: `labpipe automation setup` (watcher + nightly run).

Needs a terminal, so agents cannot run it.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from labpipe import agents, automation
from labpipe.doctor import _read_only
from labpipe.roview import commands as readonly_view_commands
from labpipe.safety import PROJECT_ROOT, SetupError, load_pipeline_config, set_local_block

STEPS = ("raw-data", "sandbox", "agents", "automation")


def _ask(question: str, default: str = "") -> str:
    answer = input(f"{question} [{default}]: " if default else f"{question}: ").strip()
    return answer or default


def _yes(question: str, default: bool) -> bool:
    return _ask(f"{question} (y/n)", "y" if default else "n").lower().startswith("y")


def _heading(number: int, title: str) -> None:
    print(f"\n── {number}/4 {title} " + "─" * max(0, 60 - len(title)))


def _show_steps(plan: dict[str, Any]) -> None:
    for step in plan["steps"]:
        print(f"\n  {step['title']}  (run as: {step['run_as']})")
        for command in step["commands"]:
            print(f"      {command}")
        if step["explain"]:
            print(f"    {step['explain']}")
    if not plan.get("tested"):
        print("\n  (These instructions are untested on this system; docs/SAFETY.md has details.)")


def _wait_for(path: Path) -> tuple[bool | None, str]:
    input("\nRun these in another terminal, then press Enter here to check (Ctrl+C stops). ")
    if not path.is_dir():
        return False, f"{path} does not exist (yet)"
    if platform.system() == "Windows":  # ACLs cannot be checked without writing
        return ((True, "confirmed by you") if _yes("Did the write test fail as it should?", False)
                else (False, "the write test did not fail"))
    return _read_only(path)


def _save_raw_root(config: dict, root_id: str, path: str, protect: list[str]) -> None:
    old = [root["id"] for root in config.get("raw_roots") or []]
    protected = list(dict.fromkeys([*(config.get("protected_paths") or []), *protect]))
    set_local_block("raw_roots", [{"id": root_id, "path": path}])
    set_local_block("protected_paths", protected)
    print(f"\nSaved to config/local.yaml: raw root {root_id!r} = {path}"
          + (f"; never written to: {', '.join(protected)}" if protected else ""))
    if old and old[0] != root_id and (PROJECT_ROOT / config["manifest"]).exists():
        print(f"The raw root's name changed from {old[0]!r}. If it is the same data, keep "
              f"existing results attached with: labpipe roots rename {old[0]} {root_id}")


def raw_data(config: dict) -> None:
    roots = config.get("raw_roots") or []
    if len(roots) > 1:
        print("Several raw roots are configured; edit config/local.yaml directly to change them.")
        return
    if roots:
        read_only, how = _read_only(Path(roots[0]["path"]))
        print(f"Currently: {roots[0]['path']} ({how})")
        if not _yes("Change it?", read_only is not True):
            return
    print("labpipe needs one thing: a folder it can only read. How your files get there "
          "(copied by hand, exported from the instrument PC, a network drive, a sync client) "
          "is up to you.")
    source = _ask("Folder that holds your raw instrument files")
    if not source:
        return
    path = Path(os.path.expanduser(source)).resolve()
    if not path.is_dir():
        print(f"{path} is not a folder; nothing changed.")
        return
    root_id = _ask("A short, fixed name for this data location (recorded in manifest.json)",
                   roots[0]["id"] if roots else "lab")
    read_only, how = _read_only(path)
    if read_only is True:
        print(f"This folder is already read-only for labpipe ({how}).")
        _save_raw_root(config, root_id, str(path), [])
        return
    windows = platform.system() == "Windows"
    print("\nThis folder is writable. Choose how labpipe gets a read-only folder:\n"
          "  1  a read-only view of it (recommended; needs admin rights once). The files "
          "stay where they are and stay writable for you; labpipe reads the view.\n"
          + ("" if windows else
             "  2  make the folder itself read-only (no admin rights, but weaker: anything "
             "running as you could make it writable again)\n")
          + "  s  skip for now")
    choice = _ask("Choice", "1")
    if choice == "1":
        plan = readonly_view_commands(str(path))
        _show_steps(plan)
        if plan.get("alternative"):
            alt = plan["alternative"]
            print(f"\n  Alternative: {alt['title']}")
            for command in alt["commands"]:
                print(f"      {command}")
            print(f"    {alt['explain']}")
        view = _ask("\nPath of the read-only view once it exists", plan["view"])
        read_only, how = _wait_for(Path(view))
        if read_only is not True:
            print(f"Not read-only yet: {how}. Nothing saved; rerun `labpipe setup --only "
                  f"raw-data` when it is.")
            return
        _save_raw_root(config, root_id, view, [str(path)])
    elif choice == "2" and not windows:
        print("\n  Remove write permission (you run this; to add files later: "
              "`chmod -R u+w`, copy, then this again):")
        print(f"      chmod -R a-w {_quote(str(path))}")
        read_only, how = _wait_for(path)
        if read_only is not True:
            print(f"Not read-only yet: {how}. Nothing saved.")
            return
        _save_raw_root(config, root_id, str(path), [str(path)])


def _quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


def sandbox() -> None:
    system = platform.system()
    claude = shutil.which("claude")
    print(f"Claude Code: {claude or 'not found on PATH'}"
          + ("" if claude else " (needed for the agents and automatic processing; see "
             "README → Prerequisites, then rerun this step)"))
    if system == "Linux":
        missing = [tool for tool in ("bwrap", "socat") if not shutil.which(tool)]
        if not missing:
            print("Sandbox prerequisites present (bubblewrap, socat): unattended runs can "
                  "start sandboxed.")
            return
        print(f"Missing for the sandbox: {', '.join(missing)}. Unattended runs refuse to "
              "start without it. Install (you run this, it needs admin rights):\n"
              "      sudo dnf install bubblewrap socat        # Fedora\n"
              "      sudo apt install bubblewrap socat        # Debian, Ubuntu")
    elif system == "Darwin":
        print("macOS has the sandbox built in (Seatbelt); nothing to install.")
    else:
        print("Windows has no native Claude Code sandbox: use the agents interactively here, "
              "and run unattended processing inside WSL2 (docs/SAFETY.md).")


def agent_models(config: dict) -> None:
    current = config.get("agents") or {}
    print("Agents run labpipe commands and write notes; they never compute numbers. Two "
          "roles: the supervisor runs commands and keeps the books (a small, fast model is "
          "enough); the analyst looks at plots and writes notes for people (a stronger model "
          "writes better notes). `labpipe notes verify` rejects invented numbers either way.")
    harness = _ask("Agent harness: claude-code, opencode, or none",
                   current.get("harness") or "claude-code")
    if harness == "none":
        return
    hint = ("haiku, sonnet, opus, inherit (= the session's model) or anthropic/<model>"
            if harness == "claude-code" else "provider/model or inherit")
    print(f"Models: {hint}.")
    defaults = {"supervisor": "haiku", "analyst": "sonnet"} if harness == "claude-code" else {}
    models = dict(current.get("models") or {})
    for role in sorted({role["role"] for role in agents.load_roles()}, reverse=True):
        models[role] = _ask(f"Model for the {role}", str(models.get(role) or
                                                          defaults.get(role, "inherit")))
    try:
        agents.settings({"agents": {"harness": harness, "models": models}})
        for role in agents.load_roles():
            agents._render(role, harness, models)
    except agents.AgentConfigError as exc:
        print(f"Not saved: {exc}")
        return
    set_local_block("agents", {"harness": harness, "models": models})
    result = agents.sync(load_pipeline_config())
    print(f"Saved to config/local.yaml → agents; wrote "
          + ", ".join(f"{w['file']} ({w['model']})" for w in result["written"]))


def run(only: str | None = None) -> int:
    if not sys.stdin.isatty():
        print("Run `labpipe setup` yourself in a terminal: it asks you questions and some "
              "steps are your decision, so agents cannot run it.")
        return 2
    try:
        load_pipeline_config()
    except SetupError:
        print("config/local.yaml is missing: run `labpipe init` first.")
        return 1
    print("labpipe setup. Every step can be skipped and rerun later "
          "(`labpipe setup --only <step>`); nothing that needs admin rights is run for you.")
    try:
        for number, step in enumerate(STEPS, 1):
            if only and step != only:
                continue
            titles = {"raw-data": "Raw data folder (read-only)",
                      "sandbox": "Sandbox for unattended agent runs",
                      "agents": "Agents and models",
                      "automation": "Automatic processing"}
            _heading(number, titles[step])
            config = load_pipeline_config()  # earlier steps may have changed it
            if step == "raw-data":
                raw_data(config)
            elif step == "sandbox":
                sandbox()
            elif step == "agents":
                agent_models(config)
            elif only or _yes("Set up automatic processing now (a watcher for new files and a "
                              "nightly run)?", False):
                automation.setup(config)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Settings saved so far are kept; rerun `labpipe setup` to continue.")
        return 1
    print("\nDone. `labpipe doctor` shows what is left.")
    return 0
