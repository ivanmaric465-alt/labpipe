"""`labpipe update`: bring the installation and this project to a release, safely
(docs/design/install.md → "labpipe update, step by step"; for users: docs/settings/updates.md).

    labpipe update [--check] [--preview [--limit N]] [--to vX.Y.Z] [--yes]

The command runs in the user's terminal (agents never run it). What it does, each step said in
plain words as it happens (on stderr; the final JSON summary is on stdout, as for `migrate`):

 1. fetch the release tags; show the changelog between the project's release and the target;
    `--check` stops here. A preview is offered when a release says it changes results.
 2. (`--preview`) analyse copies of the measurements with the target's code: preview.py.
 3. before switching: `notes verify` of every analysed entry (a pass is recorded with today's
    rules), then back up `labpipe.yaml`, `config/`, `manifest.json` and `lab/methods.yaml` to
    `<project>/backups/<release>/`.
 4. install the target: `git checkout --detach <tag>` in the installation's clone, then the
    target's own installer without switching or setup (`install.sh --keep-checkout`).
 5. continue with the new code: `labpipe update --resume <state file>` (the state is in
    `<project>/logs/update/<UTC>.json`), because everything after this must be done by the
    target's code, not by the code that is being replaced:
 6. settings: the migrations of the target (diff shown, asked); a rollback offers to restore
    `config/` from the backup made when the project left that release;
 7. `project sync`;  8. restart the container (`container start --replace`; native: nothing);
 9. `scan`, then what is out of date now and what to do (nothing is reanalysed);
10. `labpipe.yaml` records `labpipe_version` and an entry in `updates:`; last, a `doctor`
    summary (after the record, so that it does not say "run labpipe update" any more).

When the installation is already at the target (another project updated it) but this project's
`labpipe_version` is older (or newer, for a rollback), only 3 and 6-10 run, in this process.

Never written: raw data, anything outside `backups/`, `logs/update/`, `labpipe.yaml`, the
settings the migrations change, the generated files, and the installation itself. Backups are
never deleted. Host side: Python 3.10, standard library and PyYAML only.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from labpipe import __version__, container, migrations, preview, project, release, safety
from labpipe.provenance import utc_now
from labpipe.safety import CODE_ROOT, PROJECT_ROOT, SetupError

SCHEMA = "labpipe.update/1"
STATE_DIR = "logs/update"
DEV_MESSAGE = "dev mode: use git; releases are for installations"
UPDATE_FILE = "src/labpipe/update.py"      # a release without it cannot finish an update
# The steps that run in the target's code, in order; the state file records the last one done.
AFTER_SWITCH = ("settings", "sync", "restart", "scan", "record", "doctor")


class UpdateError(RuntimeError):
    """Something the user must know and fix; the message says what to do."""


def say(text: str = "") -> None:
    """The narrative: plain words on stderr (stdout is the final JSON)."""
    print(text, file=sys.stderr, flush=True)


def _print(value: Any) -> None:
    print(json.dumps(value, indent=1, ensure_ascii=False))


def _fail(message: str, code: int = 2, **extra: Any) -> int:
    _print({"error": message, **extra})
    return code


def ask(question: str, yes: bool) -> bool:
    """A yes/no question (default no). `--yes` answers yes and still says what it did."""
    if yes:
        say(f"{question} [y/N] y (--yes)")
        return True
    if not sys.stdin.isatty():
        raise UpdateError("`labpipe update` asks questions: run it in a terminal, or add --yes "
                          "(after you read what it would do: `labpipe update --check`)")
    print(f"{question} [y/N] ", end="", file=sys.stderr, flush=True)
    return input().strip().lower() in ("y", "yes")


def agent_tool() -> str:
    """The user's agent tool by name ("Claude Code", "Codex"), for what to tell it."""
    from labpipe.installer import HARNESSES
    try:
        harness = (safety.load_pipeline_config().get("agents") or {}).get("harness")
    except SetupError:
        harness = None
    return HARNESSES.get(harness, "your agent tool")


# ---- git and releases --------------------------------------------------------------------

def _git(app: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(app), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


def tag_name(version: str) -> str:
    return "v" + ".".join(str(n) for n in release.parse_version(version))


def project_version(project_root: Path) -> str:
    """The release the project was last set up or updated with (the installed one if unknown)."""
    recorded = project.read_project(project_root).get("labpipe_version")
    try:
        return tag_name(str(recorded))
    except ValueError:
        return tag_name(__version__)


def fetch_tags(app: Path) -> bool:
    try:
        return _git(app, "fetch", "--tags", "--quiet", "origin", timeout=90).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def changelog_entries(app: Path, tags: list[str], newest: str) -> list[dict[str, Any]]:
    """The parsed CHANGELOG.md of release `newest` (it holds every older entry too)."""
    shown = _git(app, "show", f"{newest}:CHANGELOG.md")
    text = shown.stdout if shown.returncode == 0 else ""
    if not text and (app / "CHANGELOG.md").is_file():
        text = (app / "CHANGELOG.md").read_text(encoding="utf-8")
    try:
        return release.parse_changelog(text)
    except release.ReleaseError as exc:
        say(f"Note: the changelog of {newest} could not be read ({exc}).")
        return []


def format_entries(entries: list[dict[str, Any]]) -> list[str]:
    lines = []
    for entry in entries:
        lines.append(f"{entry['version']} — {entry['date']}" + ("  (undone by going back)"
                                                                 if entry.get("undone") else ""))
        for part in release.PARTS:
            bullets = entry["sections"][part]
            lines.append(f"  {part}:" + ("" if bullets else " none"))
            lines += [f"    - {bullet}" for bullet in bullets]
        lines.append("")
    return lines


def results_bullets(entries: list[dict[str, Any]]) -> list[str]:
    return [b for e in entries for b in e["sections"]["Changes results"]]


# ---- state, backups, writing -------------------------------------------------------------

def write_text(path: Path, text: str) -> None:
    try:
        config = safety.load_pipeline_config()
    except SetupError:
        config = {"raw_roots": [], "protected_paths": []}
    safety.atomic_write_text(path, text, config, pipeline=False)


def save_state(state: dict[str, Any]) -> None:
    write_text(Path(state["file"]), json.dumps(state, indent=1, ensure_ascii=False) + "\n")


def load_state(path: str) -> dict[str, Any]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"cannot read the update state {path}: {exc}") from exc
    if state.get("schema") != SCHEMA:
        raise UpdateError(f"{path} is not a labpipe update state")
    return state


def new_backup_folder(project_root: Path, tag: str) -> Path:
    """backups/<release>/, or -2, -3, ... when it exists (nothing is overwritten)."""
    folder, number = project_root / "backups" / tag, 2
    while folder.exists():
        folder = project_root / "backups" / f"{tag}-{number}"
        number += 1
    return folder


def make_backup(project_root: Path, folder: Path) -> list[str]:
    """Copy what an update or a rollback may change: labpipe.yaml, config/, manifest.json and
    lab/methods.yaml. Returns what was copied (relative, posix)."""
    copied = []
    folder.mkdir(parents=True)
    for name in (safety.PROJECT_FILE, "manifest.json", "lab/methods.yaml"):
        source = project_root / name
        if source.is_file():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, folder / name)
            copied.append(name)
    if (project_root / "config").is_dir():
        shutil.copytree(project_root / "config", folder / "config")
        copied.append("config/")
    return copied


def latest_backup(project_root: Path, tag: str) -> Path | None:
    """The newest backup made when the project left release `tag` (backups/<tag>, -2, ...)."""
    found = [p for p in (project_root / "backups").glob(f"{tag}*") if p.is_dir()
             and (p.name == tag or p.name[len(tag):].startswith("-") and p.name[len(tag) + 1:].isdigit())]
    return max(found, key=lambda p: (len(p.name), p.name), default=None)


# ---- notes: record the passes with today's rules -----------------------------------------

def _project_command(project_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    """`labpipe <args>` of the running code, from the project folder (container mode forwards)."""
    return subprocess.run([sys.executable, "-m", "labpipe.cli", *args], cwd=str(project_root),
                          capture_output=True, text=True, encoding="utf-8")


def verify_notes(project_root: Path) -> dict[str, int]:
    """`labpipe notes verify` for every analysed entry that has notes and no pass recorded with
    the current rules (`notes_check`), so the passes are on record before the rules change."""
    from labpipe import notes
    try:
        entries = json.loads((project_root / "manifest.json").read_text(encoding="utf-8"))["entries"]
    except (OSError, ValueError, KeyError):
        return {"checked": 0, "passed": 0, "not_passing": 0, "recorded_already": 0}
    todo, recorded = [], 0
    for entry in entries.values():
        if (entry.get("status") in ("done", "stale") and entry.get("output_dir")
                and (project_root / entry["output_dir"] / "notes.md").is_file()):
            if (entry.get("notes_check") or {}).get("rules") == notes.CHECKS_VERSION:
                recorded += 1
            else:
                todo.append(entry["entry_id"])
    passed = 0
    for number, eid in enumerate(todo):
        done = _project_command(project_root, ["notes", "verify", "--entry", eid])
        if done.returncode == 2 and "container not running" in done.stdout:
            say("  The container is not running, so the notes cannot be checked now "
                "(`labpipe container start`). Going on without recording them.")
            return {"checked": number, "passed": passed, "not_passing": number - passed,
                    "recorded_already": recorded, "skipped": len(todo) - number}
        passed += done.returncode == 0
    return {"checked": len(todo), "passed": passed, "not_passing": len(todo) - passed,
            "recorded_already": recorded}


# ---- the installer (step 4) --------------------------------------------------------------

def install_mode(home: Path) -> str:
    """How the installation was made: "full" (with the analysis libraries) or "host"."""
    try:
        mode = json.loads((home / "install.json").read_text(encoding="utf-8-sig")).get("mode")
        if mode in ("full", "host"):
            return mode
    except (OSError, ValueError, AttributeError):
        pass
    # an installation from before the choice was recorded: does its environment have numpy?
    probe = subprocess.run([sys.executable, "-c", "import numpy"], capture_output=True)
    return "full" if probe.returncode == 0 else "host"


def installer_command(app: Path, mode: str, windows: bool | None = None
                      ) -> tuple[list[str], dict[str, str]]:
    """The target's own installer, told to keep the checkout and not to ask its questions, and
    the environment that makes it reinstall into THIS environment the way the first install did.
    LABPIPE_SWITCHED also stops installers that predate --keep-checkout from switching."""
    windows = os.name == "nt" if windows is None else windows
    env = {"LABPIPE_CHANNEL": "keep", "LABPIPE_SWITCHED": "1", "LABPIPE_NO_SETUP": "1"}
    if mode == "full":
        env["LABPIPE_FULL"] = "1"
    if sys.prefix != sys.base_prefix:          # running in the installation's venv
        env["LABPIPE_VENV"] = sys.prefix
        link = shutil.which("labpipe")
        mine = Path(sys.prefix) / (("Scripts" if windows else "bin")) / (
            "labpipe.exe" if windows else "labpipe")
        if link and os.path.realpath(link) == os.path.realpath(mine):
            env["LABPIPE_BIN"] = str(Path(link).parent)
    if windows:
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(app / "install.ps1"), "-NoSetup", "-KeepCheckout",
                *(["-Full"] if mode == "full" else [])], env
    return ["bash", str(app / "install.sh"), "--no-setup", "--keep-checkout",
            *(["--full"] if mode == "full" else [])], env


# ---- the run -----------------------------------------------------------------------------

def main(args) -> int:
    """`labpipe update` (cli.cmd_update)."""
    try:
        if args.resume:
            return resume(args.resume)
        return run(args)
    except UpdateError as exc:
        return _fail(str(exc))
    except preview.PreviewError as exc:
        return _fail(f"preview: {exc}")
    except (migrations.MigrationError, project.ProjectError, SetupError, container.ContainerError) as exc:
        return _fail(str(exc))


def run(args) -> int:
    if safety.dev_mode() or not safety.is_installation(CODE_ROOT):
        return _fail(DEV_MESSAGE)
    if safety.NO_PROJECT:
        return _fail(safety.NO_PROJECT_MESSAGE)
    app, home, root = Path(CODE_ROOT), safety.install_home(), Path(PROJECT_ROOT)
    if not (app / ".git").exists():
        return _fail(f"{app} is not a git clone, so there are no releases to update to")
    if args.limit and not args.preview:
        say("Note: --limit applies to --preview only.")

    # 1. what is available
    say("Looking for releases ...")
    if not fetch_tags(app):
        say("Could not reach the release server (offline?): using the release tags already here.")
    tags = release.tags(app)
    if not tags:
        return _fail("no release found in this installation (no vX.Y.Z tags)")
    target = tags[-1]
    if args.to:
        wanted = args.to if args.to.startswith("v") else "v" + args.to
        if wanted not in tags:
            return _fail(f"{args.to} is not a release of this installation; the newest are "
                         + ", ".join(tags[-5:]))
        target = wanted
    head = _git(app, "rev-parse", "HEAD").stdout.strip()
    at_target = head == _git(app, "rev-parse", f"{target}^{{commit}}").stdout.strip()
    base = project_version(root)
    installed = release.current(app)
    installed_text = installed["tag"] or f"v{__version__} (not at a release tag)"
    base_v, target_v = release.parse_version(base), release.parse_version(target)
    if at_target and base_v == target_v:
        say(f"Up to date: the installation and this project are at {target}.")
        _print({"up_to_date": True, "installed": installed_text, "project": base,
                "target": target})
        return 0
    rollback = target_v < base_v
    project_only = at_target
    if not project_only and _git(app, "cat-file", "-e", f"{target}:{UPDATE_FILE}").returncode:
        return _fail(f"{target} cannot finish an update (it has no `labpipe update`): "
                     "go to a newer release")
    newest = max(target, base, key=release.parse_version)
    entries = changelog_entries(app, tags, newest if newest in tags else tags[-1])
    changes = release.between(entries, base, target)
    say(f"Installed: {installed_text}.  This project: {base}.  "
        f"{'Going back to' if rollback else 'Target:'} {target}"
        + (" (the newest release)." if target == tags[-1] else "."))
    if project_only:
        say(f"The installation is already at {target} (another project updated it); "
            f"only this project is {'brought back' if rollback else 'updated'}.")
    say()
    say(("These changes will be undone:" if rollback else "What changes:") if changes
        else "The changelog has no entry for this step.")
    for line in format_entries(changes):
        say(line)
    changing = results_bullets(changes)
    summary = {"installed": installed_text, "project": base, "target": target,
               "up_to_date": False, "direction": "rollback" if rollback else "update",
               "installation_already_at_target": project_only,
               "changes_results": changing, "releases": [e["version"] for e in changes]}
    if not project_only and not args.check:
        dirty = [line for line in _git(app, "status", "--porcelain").stdout.splitlines() if line]
        if dirty:
            return _fail(f"{app} has local changes, so labpipe will not switch its code: "
                         + "; ".join(line.strip() for line in dirty[:8])
                         + f". Undo or commit them (`git -C {app} status`), then run "
                         "`labpipe update` again.")

    # 2. preview (asked when a release says it changes results)
    previewed = False
    wants = args.preview
    if project_only and args.preview:
        say("Preview skipped: this installation already runs the target, so there is no earlier "
            "code here to compare with.")
        wants = False
    elif changing and not args.preview and not args.check and not args.yes:
        say(f"{'These releases change results' if len(changing) > 1 else 'This release changes results'} "
            "(see above). A preview analyses copies of your measurements with the new code, "
            "by code only, and shows what would change. Nothing in the project changes.")
        wants = ask("Preview now?", False)
    if wants:
        say()
        result = preview.run(root, app, home, target, base, limit=args.limit, say=say)
        previewed = True
        say()
        for line in result["summary"]:
            say("  " + line)
        for problem in result["problems"]:
            say("  Problem: " + problem)
        say(f"  (details: {result['file']})")
        say()
        summary["preview"] = {"file": result["file"], "summary": result["summary"]}

    if args.check:
        summary["next"] = (f"labpipe update{' --to ' + args.to if args.to else ''}"
                           + ("  (add --preview to see what would change in your numbers)"
                              if changing and not previewed else ""))
        _print(summary)
        return 0

    if not ask((f"Go back to {target}?" if rollback else f"Update to {target}?"), args.yes):
        say("Nothing changed.")
        _print({**summary, "changed": False})
        return 1

    # 3. before switching
    say()
    say("Checking the notes with today's rules, so that their passes are on record before the "
        "rules can change ...")
    checked = verify_notes(root)
    say(f"  {checked['checked']} checked, {checked['passed']} passed"
        + (f", {checked['not_passing']} do not pass (see `labpipe notes verify --entry ID`)"
           if checked["not_passing"] else "")
        + (f"; {checked['recorded_already']} already recorded" if checked["recorded_already"] else "")
        + ".")
    backup = new_backup_folder(root, base)
    copied = make_backup(root, backup)
    say(f"Backed up {', '.join(copied)} to {backup.relative_to(root).as_posix()}/ "
        "(backups are never deleted).")
    stamp = utc_now().replace(":", "").replace("-", "")
    state = {"schema": SCHEMA, "file": (root / STATE_DIR / f"{stamp}.json").as_posix(),
             "project": root.as_posix(), "from": base, "to": target, "utc": utc_now(),
             "direction": summary["direction"], "project_only": project_only,
             "backup": backup.relative_to(root).as_posix(), "preview": previewed,
             "yes": bool(args.yes), "notes": checked, "step": "backup", "report": {}}
    save_state(state)

    if project_only:
        return finish(state)

    # 4. install the target, 5. continue with its code
    say()
    say(f"Switching the installation's code to {target} ({app}) ...")
    switched = _git(app, "-c", "advice.detachedHead=false", "checkout", "--detach", target)
    if switched.returncode:
        raise UpdateError(f"could not switch to {target}: {switched.stderr.strip()}; nothing "
                          f"else was changed (the backup is in {state['backup']}/)")
    state["step"] = "switched"
    save_state(state)
    command, extra = installer_command(app, install_mode(home))
    say(f"Reinstalling the host part with {target}'s own installer ({' '.join(command[1:])}) ...")
    installed_ok = subprocess.run(command, cwd=str(home) if home.is_dir() else None,
                                  env={**os.environ, "LABPIPE_HOME": str(home), **extra},
                                  stdout=sys.stderr).returncode == 0
    if not installed_ok:
        raise UpdateError(f"the installer failed (its messages are above). The code is now "
                          f"{target}. Fix what it says, then run: labpipe update --resume "
                          f"{state['file']}")
    state["step"] = "reinstalled"
    save_state(state)
    say(f"Continuing with the code of {target} ...")
    say()
    return subprocess.run([sys.executable, "-m", "labpipe.cli", "update", "--resume",
                           state["file"]], cwd=str(root)).returncode


def resume(state_file: str) -> int:
    """`--resume STATE`: what comes after the switch, run by the code that was switched to."""
    state = load_state(state_file)
    if Path(state["project"]).resolve() != Path(PROJECT_ROOT).resolve():
        raise UpdateError(f"this update belongs to the project {state['project']}: run it in "
                          "that folder")
    have = release.current(CODE_ROOT)["tag"]
    if have != state["to"] and not state.get("project_only"):
        raise UpdateError(f"the installation's code is at {have or 'no release'}, but this update "
                          f"is to {state['to']}: nothing was done")
    return finish(state)


def finish(state: dict[str, Any]) -> int:
    """Steps 6-10 (in the code of the target), from the one after the last recorded."""
    root = Path(state["project"])
    steps = {"settings": settings_step, "sync": sync_step, "restart": restart_step,
             "scan": scan_step, "record": record_step, "doctor": doctor_step}
    done = AFTER_SWITCH.index(state["step"]) + 1 if state["step"] in AFTER_SWITCH else 0
    try:
        for name in AFTER_SWITCH[done:]:
            say()
            steps[name](state, root)
            state["step"] = name
            save_state(state)
    except (UpdateError, migrations.MigrationError, project.ProjectError, SetupError,
            OSError) as exc:
        return _fail(f"{exc}", resume=f"labpipe update --resume {state['file']}",
                     step=f"after {state['step']}")
    report = state["report"]
    say()
    say(f"Done: this project is at {state['to']}"
        + (" (the installation was switched too)" if not state["project_only"] else "") + ".")
    _print({"updated": True, "from": state["from"], "to": state["to"],
            "direction": state["direction"], "backup": state["backup"],
            "preview": state["preview"], "notes": state["notes"],
            "settings_migrated": report.get("migrated", []),
            "settings_restored": report.get("restored", []),
            "stale": report.get("stale", {}), "doctor": report.get("doctor"),
            "state": Path(state["file"]).relative_to(root).as_posix()})
    return 0


# ---- steps 6-10 --------------------------------------------------------------------------

def _diff(before: str, after: str, name: str, limit: int = 40) -> list[str]:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      f"a/{name}", f"b/{name}", lineterm=""))
    return lines[:limit] + ([f"... ({len(lines) - limit} more lines)"] if len(lines) > limit else [])


def restore_plan(root: Path, source: Path) -> list[dict[str, Any]]:
    """The files of `source/config/` that differ from the project's (or are missing there)."""
    items = []
    for path in sorted(p for p in (source / "config").rglob("*") if p.is_file()):
        relative = path.relative_to(source).as_posix()
        now = root / relative
        before = now.read_text(encoding="utf-8", errors="replace") if now.is_file() else None
        after = path.read_text(encoding="utf-8", errors="replace")
        if before != after:
            items.append({"file": relative, "missing": before is None,
                          "diff": _diff(before or "", after, relative)})
    return items


def settings_step(state: dict[str, Any], root: Path) -> None:
    say("Settings:")
    report = state["report"]
    if state["direction"] == "rollback":
        source = latest_backup(root, state["to"])
        if source is None or not (source / "config").is_dir():
            say(f"  There is no backup of your settings from when this project left {state['to']}, "
                "so they stay as they are. They may use names that release does not know: "
                "`labpipe doctor` will say.")
        else:
            planned = restore_plan(root, source)
            if not planned:
                say(f"  Your settings equal the backup {source.relative_to(root).as_posix()}/: "
                    "nothing to restore.")
            else:
                say(f"  The backup {source.relative_to(root).as_posix()}/ (made when this project "
                    f"left {state['to']}) differs from your settings in {len(planned)} file(s):")
                for item in planned:
                    say(f"  {item['file']}" + ("  (missing now)" if item["missing"] else ""))
                    for line in item["diff"]:
                        say("    " + line)
                if ask(f"  Restore these {len(planned)} file(s)? Your present ones were saved in "
                       f"{state['backup']}/ a moment ago.", state["yes"]):
                    for item in planned:
                        target = root / item["file"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        write_text(target, (source / item["file"]).read_text(encoding="utf-8"))
                    report["restored"] = [i["file"] for i in planned]
                    say(f"  Restored {len(planned)} file(s).")
                else:
                    say("  Left as they are.")
    planned = migrations.plan(root)
    if not planned:
        say("  The settings files use the current names.")
        return
    say(f"  {len(planned)} settings file(s) use names this release changed:")
    for item in planned:
        say(f"  {item['file']}: " + "; ".join(m["description"] for m in item["migrations"])
            + ("" if item["comments_kept"] else "   (comments in this file are not kept)"))
        for line in item["diff"].splitlines():
            say("    " + line)
    if not ask(f"  Change {len(planned)} file(s)? The originals are kept in "
               f"{state['backup']}/migrated/.", state["yes"]):
        say("  Not changed. The old names keep working for one release, with a warning; "
            "`labpipe migrate --apply` does it later.")
        return
    written = migrations.apply(root, planned, root / state["backup"] / "migrated")
    report["migrated"] = written
    say(f"  Changed {len(written)} file(s).")


def sync_step(state: dict[str, Any], root: Path) -> None:
    result = project.sync()
    say(f"Generated files (AGENTS.md, CLAUDE.md, .labpipe/, the agent files): "
        f"{len(result.get('written') or [])} written, {len(result.get('removed') or [])} removed, "
        f"{result.get('unchanged', 0)} unchanged.")
    for problem in result.get("problems") or []:
        say(f"  Problem: {problem}")


def restart_step(state: dict[str, Any], root: Path) -> None:
    if container.local_container_block() is None:
        say("Native mode: nothing to restart.")
    else:
        say("Restarting the container with the new image (building it first if needed; the old "
            "image stays) ...")
        done = subprocess.run([sys.executable, "-m", "labpipe.cli", "container", "start",
                               "--replace"], cwd=str(root), stdout=subprocess.PIPE,
                              stderr=sys.stderr, text=True, encoding="utf-8")
        if done.returncode:
            raise UpdateError("the container did not start: "
                              + (done.stdout.strip() or "see above")[-400:]
                              + ". Fix it, then `labpipe container start --replace`; the update "
                              f"goes on with: labpipe update --resume {state['file']}")
        say("  Container started.")


def doctor_step(state: dict[str, Any], root: Path) -> None:
    say("labpipe doctor:")
    done = _project_command(root, ["doctor"])
    try:
        checks = preview._json_from(done.stdout)
    except ValueError:
        say("  doctor gave no readable answer: run `labpipe doctor` yourself.")
        return
    todo = [c for c in checks["checks"] if c["status"] != "ok"]
    state["report"]["doctor"] = {"ready": checks["ready"], "attention": [
        {"check": c["check"], "status": c["status"], "message": c["message"]} for c in todo]}
    say(f"  {len(todo)} item(s) need attention:" if todo else "  ready.")
    for item in todo:
        say(f"  - [{item['status']}] {item['message']}"
            + (f"  -> {item['next']}" if item.get("next") else ""))


def scan_step(state: dict[str, Any], root: Path) -> None:
    say("Looking for analyses that are out of date now (labpipe scan) ...")
    done = _project_command(root, ["scan"])
    if done.returncode:
        say("  The scan failed: " + (done.stdout.strip() or done.stderr.strip())[-300:])
    try:
        entries = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["entries"]
    except (OSError, ValueError, KeyError):
        entries = {}
    stale: dict[str, int] = {}
    for entry in entries.values():
        if entry.get("status") == "stale":
            stale[entry["method"]] = stale.get(entry["method"], 0) + 1
    state["report"]["stale"] = stale
    if not stale:
        say("  None: every analysis is current. Nothing to redo.")
        return
    say("  Out of date (the calculation of the method changed; the old results are kept and "
        "still valid until you redo them):")
    for method, n in sorted(stale.items()):
        say(f"  - {method}: {n} analys{'es' if n != 1 else 'is'}")
    say(f"  To redo them: `labpipe requeue --stale`, then `labpipe analyze --pending` "
        f"(or tell {agent_tool()}: redo the out-of-date analyses). Nothing is reanalysed "
        "automatically; the old results go to _superseded/.")


def record_step(state: dict[str, Any], root: Path) -> None:
    path = root / safety.PROJECT_FILE
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    header = []
    for line in text.splitlines():              # comment lines at the top are kept
        if not line.startswith("#"):
            break
        header.append(line)
    to = state["to"].lstrip("v")
    data["labpipe_version"] = to
    updates = data.get("updates") if isinstance(data.get("updates"), list) else []
    updates.append({"from": state["from"].lstrip("v"), "to": to, "utc": state["utc"],
                    "backup": state["backup"], "preview": state["preview"]})
    data["updates"] = updates
    write_text(path, "".join(f"{h}\n" for h in header)
               + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    say(f"Recorded in labpipe.yaml: labpipe_version {to}.")
