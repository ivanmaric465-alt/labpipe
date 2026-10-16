"""`labpipe setup`: guided setup in the user's own terminal (the installers offer it).

Five steps, each optional and safe to rerun; current settings are the defaults:

1. raw-data: the user's data folder (where they add files) and the read-only
   folder labpipe reads it through. Commands that need admin rights or change
   permissions on the raw data are printed for the user to run; this never runs
   them. It then checks the result and writes `raw_roots` (with `source`, the
   data folder) and `protected_paths` into config/local.yaml.
2. files: what labpipe recognises there (`labpipe inventory`); asks whether that
   is right, and what to do with data already in the folder (analyse it, or
   record it as baseline and analyse only new files).
3. sandbox: are the prerequisites of the Claude Code sandbox present (needed
   for unattended runs)?
4. agents: harness and a model per role → `agents:`, then `labpipe agents sync`.
5. automation: `labpipe automation setup` (watcher + nightly run).

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

STEPS = ("raw-data", "files", "sandbox", "agents", "automation")


def _ask(question: str, default: str = "") -> str:
    answer = input(f"{question} [{default}]: " if default else f"{question}: ").strip()
    return answer or default


def _yes(question: str, default: bool) -> bool:
    return _ask(f"{question} (y/n)", "y" if default else "n").lower().startswith("y")


def _heading(number: int, title: str) -> None:
    print(f"\n── {number}/{len(STEPS)} {title} " + "─" * max(0, 60 - len(title)))


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


def _save_raw_root(config: dict, root_id: str, path: str, source: str,
                   protect: list[str]) -> None:
    old = [root["id"] for root in config.get("raw_roots") or []]
    protected = list(dict.fromkeys([*(config.get("protected_paths") or []), *protect]))
    set_local_block("raw_roots", [{"id": root_id, "path": path, "source": source}])
    set_local_block("protected_paths", protected)
    print(f"\nSaved to config/local.yaml:\n  you add files to:        {source}\n"
          f"  labpipe reads them from: {path} (read-only)"
          + (f"\n  never written to:        {', '.join(protected)}" if protected else ""))
    if old and old[0] != root_id and (PROJECT_ROOT / config["manifest"]).exists():
        print(f"The raw root's name changed from {old[0]!r}. If it is the same data, keep "
              f"existing results attached with: labpipe roots rename {old[0]} {root_id}")


def raw_data(config: dict) -> None:
    roots = config.get("raw_roots") or []
    if len(roots) > 1:
        print("Several raw roots are configured; edit config/local.yaml directly to change them.")
        return
    if roots:
        from labpipe.inventory import data_folder
        read_only, how = _read_only(Path(roots[0]["path"]))
        print(f"Currently:\n  you add files to:        {data_folder(config, roots[0]) or '(not recorded)'}"
              f"\n  labpipe reads them from: {roots[0]['path']} ({how})")
        if not _yes("Change it?", read_only is not True):
            return
    print("Two folders are involved:\n"
          "  - your DATA FOLDER: where you put instrument files (by hand, from the instrument PC,\n"
          "    a network drive or a sync client). It stays writable for you.\n"
          "  - a READ-ONLY folder that shows the same files; labpipe only ever reads from there.\n"
          "Recommended layout inside the data folder (a convention, not a requirement):\n"
          "    XRD/  SEM/  EDS/  Echem/   with the sample ID at the start of each file name.")
    source = _ask("Your data folder")
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
        print(f"This folder is already read-only for labpipe ({how}), so it is used as is.")
        _save_raw_root(config, root_id, str(path), str(path), [])
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
        view = _ask("\nThe read-only folder labpipe will read from", plan["view"])
        read_only, how = _wait_for(Path(view))
        if read_only is not True:
            print(f"Not read-only yet: {how}. Nothing saved; rerun `labpipe setup --only "
                  f"raw-data` when it is.")
            return
        _save_raw_root(config, root_id, view, str(path), [str(path)])
    elif choice == "2" and not windows:
        print("\n  Remove write permission (you run this; to add files later: "
              "`chmod -R u+w`, copy, then this again):")
        print(f"      chmod -R a-w {_quote(str(path))}")
        read_only, how = _wait_for(path)
        if read_only is not True:
            print(f"Not read-only yet: {how}. Nothing saved.")
            return
        _save_raw_root(config, root_id, str(path), str(path), [str(path)])


def files(config: dict) -> None:
    from labpipe import scanner
    from labpipe.inventory import describe, inventory
    if not config.get("raw_roots"):
        print("No data folder set up yet (step raw-data).")
        return
    result = inventory(config)
    print(describe(result))
    available = [r for r in result["roots"] if r["available"]]
    if not _yes("\nDoes this look right?", True):
        print("Open this folder in your agent and say \"labpipe does not recognise my files "
              "correctly\". It goes through `labpipe inventory` with you and can adapt the "
              "sample-ID rule, or set up a new analysis method for files no method reads yet.")
    elif any(r["not_recognised"] for r in available):
        print("Files no method reads are left alone. To analyse some of them, ask your agent "
              "to add a method for them (docs/ADDING_A_METHOD.md).")
    fresh = [r for r in available if r["recognised"] and not r["already_in_manifest"]]
    if not fresh:
        return
    print("\nThe folder already holds data. What should labpipe do with it?\n"
          "  1  analyse all of it (the analysis is code and free; AI notes follow in batches)\n"
          "  2  only files added from now on: existing files are recorded as 'baseline' and\n"
          "     not analysed; queue them any time with `labpipe requeue --baseline`\n"
          "  s  decide later")
    choice = _ask("Choice", "2")
    if choice not in ("1", "2"):
        return
    summary = scanner.scan(config, baseline=choice == "2")
    print("Recorded: " + ", ".join(f"{n} {status}" for status, n in summary["counts"].items()))
    if summary["counts"].get("settling"):
        print("Files changed in the last minutes are skipped until they settle; they will "
              "count as new.")
    if choice == "1":
        print("Next: `labpipe analyze --pending` analyses them (code only); your agent or "
              "automatic processing then writes the notes.")


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
            titles = {"raw-data": "Data folder and its read-only view",
                      "files": "What labpipe recognises",
                      "sandbox": "Sandbox for unattended agent runs",
                      "agents": "Agents and models",
                      "automation": "Automatic processing"}
            _heading(number, titles[step])
            config = load_pipeline_config()  # earlier steps may have changed it
            if step == "raw-data":
                raw_data(config)
            elif step == "files":
                files(config)
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
