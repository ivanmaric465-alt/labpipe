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
from labpipe.safety import CODE_ROOT, PROJECT_ROOT, SetupError, load_pipeline_config, set_local_block

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
          "Any layout inside the data folder works (by technique, by sample, by date...):\n"
          "labpipe finds files by type and reads the sample ID from the start of each file\n"
          "name (a rule you can change).")
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
             "README → Requirements, then rerun this step)"))
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


def _codex_models() -> list[str]:
    """Models this Codex installation lists (`codex debug models`), for the prompt only."""
    import json
    import subprocess
    codex = shutil.which("codex")
    if not codex:
        return []
    try:
        listing = json.loads(subprocess.run([codex, "debug", "models"], capture_output=True,
                                            text=True, timeout=30).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    items = listing if isinstance(listing, list) else listing.get("models", [])
    return [m["slug"] for m in items if isinstance(m, dict) and m.get("visibility") == "list"
            and m.get("slug")]


ROLE_ORDER = ("supervisor", "analyst", "postdoc")
ROLE_TEXT = {
    "supervisor": "runs the pipeline commands, hands each measurement to its analyst and keeps "
                  "the books; no science. It runs on every new measurement, so a small, fast "
                  "model is enough.",
    "analyst": "the method analysts look at one measurement's plot and results and write short "
               "notes. They also run on every new measurement; a stronger model writes better "
               "notes.",
    "postdoc": "reads the whole dataset, your context and literature, and answers the "
               "question you ask. It runs only when you ask, but one run can cost more than a "
               "whole batch of notes; this is where a strong model pays off.",
}


def recommended(harness: str) -> dict[str, dict[str, str]]:
    """Per role {model, reasoning?} from config/pipeline.yaml → agents.recommended."""
    import yaml
    try:
        policy = yaml.safe_load((CODE_ROOT / "config" / "pipeline.yaml").read_text(
            encoding="utf-8")) or {}
    except OSError:
        return {}
    return ((policy.get("agents") or {}).get("recommended") or {}).get(harness) or {}


def agent_models(config: dict) -> None:
    current = config.get("agents") or {}
    print("Agents run labpipe commands and write notes; they never compute numbers "
          "(`labpipe notes verify` and `labpipe postdoc verify` reject invented ones). "
          "Three roles, each with its own model:")
    for role in ROLE_ORDER:
        print(f"  {role}: {ROLE_TEXT[role]}")
    harness = _ask("Agent harness: claude-code, codex, opencode, or none",
                   current.get("harness") or "claude-code")
    if harness == "none":
        return
    hint = {"claude-code": "haiku, sonnet, opus, inherit (= the session's model) or anthropic/<model>",
            "codex": "a Codex model (" + ", ".join(_codex_models()[:6]) + ") or inherit",
            }.get(harness, "provider/model or inherit")
    print(f"Models: {hint}. Enter takes the default in brackets (your current choice, else the recommendation).")
    advice = recommended(harness)
    models = dict(current.get("models") or {})
    reasoning = dict(current.get("reasoning") or {})
    loaded = {role["role"] for role in agents.load_roles()}
    for role in [r for r in ROLE_ORDER if r in loaded] + sorted(loaded - set(ROLE_ORDER)):
        wish = advice.get(role) or {}
        suggested = str(wish.get("model") or "inherit")
        models[role] = _ask(f"Model for the {role} (recommended: {suggested}; inherit = "
                            "the harness default)", str(models.get(role) or suggested))
        if harness == "codex":
            suggested = str(wish.get("reasoning") or "medium")
            reasoning[role] = _ask(f"Reasoning effort for the {role} (recommended: {suggested}; "
                                   "low, medium, high, xhigh)",
                                   str(reasoning.get(role) or suggested))
    block = {"harness": harness, "models": models, **({"reasoning": reasoning} if reasoning else {})}
    try:
        chosen = agents.settings({"agents": block})
        for role in agents.load_roles():
            agents._render(role, harness, models, chosen["reasoning"])
    except agents.AgentConfigError as exc:
        print(f"Not saved: {exc}")
        return
    set_local_block("agents", block)
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
