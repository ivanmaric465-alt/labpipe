"""Automatic processing: `labpipe automation watch | run | setup | status`.

* `run`: one pass. Takes a lock (a second run exits at once), scans, and starts
  the AI supervisor only if there is work (pending entries or missing notes),
  for at most `max_measurements_per_run` of them (the batch, passed to the
  agents' `labpipe pending` / `notes todo` as LABPIPE_BATCH). Notifies when
  work is found and when it is done, with what is left.
* `watch`: checks the raw roots every `check_every_seconds` (file sizes and
  modification times only, no hashing). After a change, once nothing has
  changed for `settle_minutes`, it calls `run`. If work is left after a clean
  run that made progress, the next batch runs at the next check.
* `health`: for `labpipe doctor` (services running, last run ok, nothing stuck).
* `setup`: asks the user, writes `automation:` into config/local.yaml and the
  service files for this OS (Linux: systemd user units; macOS/Windows: printed,
  untested), and installs them only after the user confirms. Needs a terminal:
  agents cannot install schedules.

Settings (config/local.yaml):

    automation:
      check_every_seconds: 60
      settle_minutes: 3            # also used by `labpipe scan`
      nightly_at: "02:00"          # safety net
      max_measurements_per_run: 10 # the rest follows in the next run
      max_budget_usd: 5            # per AI run
      claude: /home/me/.local/bin/claude
      notify: {desktop: true, command: null}   # command: ["prog", "{message}"]
"""

from __future__ import annotations

import calendar
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from labpipe import manifest as mf
from labpipe import scanner
from labpipe.locking import try_exclusive
from labpipe.notes import todo as notes_todo
from labpipe.provenance import code_version, utc_now
from labpipe.registry import classify, load_methods
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, set_local_block

DEFAULTS = {"check_every_seconds": 60, "settle_minutes": 3, "nightly_at": "02:00",
            "max_measurements_per_run": 10, "max_budget_usd": 5, "claude": None,
            "notify": {"desktop": True, "command": None}}
WAITING_TOO_LONG_HOURS = 24


def settings(config: dict) -> dict[str, Any]:
    chosen = dict(DEFAULTS)
    chosen.update(config.get("automation") or {})
    chosen["notify"] = {**DEFAULTS["notify"], **(chosen.get("notify") or {})}
    return chosen


def _log(config: dict, event: dict[str, Any]) -> None:
    path = PROJECT_ROOT / config["logs_dir"] / "automation.log"
    assert_pipeline_writable(path, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"utc": utc_now(), **event}, ensure_ascii=False) + "\n")


def notify(config: dict, message: str) -> list[str]:
    """Send `message` through the configured channels; returns the channels that worked."""
    chosen = settings(config)["notify"]
    sent = []
    if chosen.get("desktop"):
        system = platform.system()
        command = None
        if system == "Linux" and shutil.which("notify-send"):
            command = ["notify-send", "--app-name=labpipe", "labpipe", message]
        elif system == "Darwin":
            command = ["osascript", "-e",
                       f'display notification {json.dumps(message)} with title "labpipe"']
        elif system == "Windows":
            script = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object "
                      "System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]"
                      "::Information; $n.Visible = $true; $n.ShowBalloonTip(10000, 'labpipe', "
                      f"{json.dumps(message)}, 'Info'); Start-Sleep 11; $n.Dispose()")
            command = ["powershell", "-NoProfile", "-Command", script]
        if command and subprocess.run(command, capture_output=True, timeout=30).returncode == 0:
            sent.append("desktop")
    if chosen.get("command"):
        command = [str(part).replace("{message}", message) for part in chosen["command"]]
        try:
            if subprocess.run(command, capture_output=True, timeout=60).returncode == 0:
                sent.append("command")
        except (OSError, subprocess.SubprocessError):
            pass
    _log(config, {"event": "notify", "message": message, "sent": sent})
    return sent


def _work(config: dict) -> dict[str, list[str]]:
    entries = mf.load(config)["entries"].values()
    return {"pending": sorted(e["entry_id"] for e in entries if e.get("status") == "pending"),
            "notes": [row["entry_id"] for row in notes_todo(config)],
            "needs_review": sorted(e["entry_id"] for e in entries
                                   if e.get("status") == "needs_review")}


def _analyse_code_only(config: dict, reason: str) -> list[str]:
    """Methods without an analyst agent (methods.yaml `subagent: null`): analysed by code
    here, so no AI run is started for them."""
    from labpipe import pipeline
    from labpipe.report import build as build_report
    methods = load_methods()
    code_only = {name for name, spec in methods.items() if not spec.get("subagent")}
    if not code_only:
        return []
    ids = sorted(e["entry_id"] for e in mf.load(config)["entries"].values()
                 if e.get("status") == "pending" and e["method"] in code_only)
    if not ids:
        return []
    results = [pipeline.analyze_entry(config, eid) for eid in ids]
    done = [r["entry_id"] for r in results if r["status"] == "done"]
    failed = [r["entry_id"] for r in results if r["status"] == "failed"]
    build_report(config)
    _log(config, {"event": "code_only_finished", "trigger": reason, "done": done,
                  "failed": failed})
    notify(config, f"labpipe: analysed {len(done)} measurement(s) by code alone "
                   f"({_describe(config, done)})" + (f", {len(failed)} failed" if failed else "")
                   + "; see analysis/report.html")
    return done


def _harness_command(config: dict, chosen: dict) -> list[str]:
    harness = (config.get("agents") or {}).get("harness")
    if harness != "claude-code":
        raise RuntimeError(f"unattended runs are supported with harness claude-code only "
                           f"(configured: {harness!r})")
    claude = chosen.get("claude") or shutil.which("claude")
    if not claude:
        raise RuntimeError("the `claude` command was not found; set automation.claude")
    return [claude, "-p", "Run one pipeline pass.", "--agent", "supervisor",
            "--setting-sources", "project", "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--settings", ".claude/unattended.json",
            "--output-format", "stream-json", "--verbose",
            "--max-budget-usd", str(chosen["max_budget_usd"])]


def _describe(config: dict, ids: list[str]) -> str:
    entries = mf.load(config)["entries"]
    rows = [entries[i] for i in ids if i in entries]
    names = sorted({e.get("sample_id") or e["relative_path"] for e in rows})
    return ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")


def run(config: dict, *, reason: str = "manual") -> dict[str, Any]:
    chosen = settings(config)
    lock = PROJECT_ROOT / config["logs_dir"] / ".autorun.mutex"
    with try_exclusive(lock) as acquired:
        if not acquired:
            _log(config, {"event": "skipped", "reason": "another run is in progress"})
            return {"status": "skipped", "reason": "another run is in progress"}
        try:
            scan = scanner.scan(config)
        except RuntimeError as exc:  # e.g. raw root not mounted
            notify(config, f"labpipe: cannot scan the raw data ({exc}); nothing was analysed")
            _log(config, {"event": "scan_failed", "reason": str(exc), "trigger": reason})
            return {"status": "scan_failed", "reason": str(exc)}
        code_only = _analyse_code_only(config, reason)
        work = _work(config)
        if work["needs_review"] and any(c["to"] == "needs_review" for c in scan["changes"]):
            notify(config, f"labpipe: {len(work['needs_review'])} file(s) need a decision "
                           "from you (sample ID); see logs/review_queue.md")
        if not work["pending"] and not work["notes"]:
            if code_only:
                return {"status": "finished", "exit_code": 0, "done": code_only, "failed": [],
                        "remaining": 0, "ai": False}
            _log(config, {"event": "nothing_to_do", "trigger": reason})
            return {"status": "nothing_to_do"}
        queued = work["pending"] + [i for i in work["notes"] if i not in work["pending"]]
        todo_ids = queued[:max(1, int(chosen["max_measurements_per_run"]))]
        remaining = len(queued) - len(todo_ids)
        notify(config, f"labpipe: analysis started for {len(todo_ids)} measurement(s) "
                       f"({_describe(config, todo_ids)})"
                       + (f"; {remaining} more queued for the next run" if remaining else ""))
        stamp = utc_now().replace(":", "").replace("-", "")
        transcript = PROJECT_ROOT / config["logs_dir"] / "runs" / f"auto-{stamp}.jsonl"
        assert_pipeline_writable(transcript, config)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        # `labpipe pending` and `labpipe notes todo` show the agents only this batch.
        env = dict(os.environ, LABPIPE_AGENT_MODE="unattended", LABPIPE_BATCH=",".join(todo_ids))
        env.pop("CLAUDECODE", None)
        env.pop("LABPIPE_RAW_ROOT", None)
        try:
            command = _harness_command(config, chosen)
            with open(transcript, "w", encoding="utf-8") as out:
                completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env, stdout=out,
                                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           timeout=3 * 3600)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            notify(config, f"labpipe: the AI run could not start or finish ({exc})")
            _log(config, {"event": "run_failed", "reason": str(exc), "trigger": reason})
            return {"status": "run_failed", "reason": str(exc)}
        result = {}
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # stderr lines interleaved with the JSON stream
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
        entries = mf.load(config)["entries"]
        done = [i for i in todo_ids if entries.get(i, {}).get("status") in ("done", "stale")]
        failed = [i for i in todo_ids if entries.get(i, {}).get("status") == "failed"]
        review = [i for i in done if entries[i].get("review")]
        still_missing = {row["entry_id"] for row in notes_todo(config)}
        message = (f"labpipe: analysis done, {len(done)} measurement(s) ({_describe(config, done)})"
                   + (f", {len(review)} to check" if review else "")
                   + (f", {len(failed)} failed" if failed else "")
                   + (f", {len(still_missing & set(todo_ids))} without notes"
                      if still_missing & set(todo_ids) else "")
                   + "; see analysis/report.html"
                   + (f"; {remaining} left for the next run" if remaining else ""))
        notify(config, message)
        summary = {"event": "run_finished", "trigger": reason, "exit_code": completed.returncode,
                   "transcript": str(transcript.relative_to(PROJECT_ROOT)),
                   "cost_usd": result.get("total_cost_usd"), "done": done, "failed": failed,
                   "remaining": remaining}
        _log(config, summary)
        return {"status": "finished", **summary}


def _snapshot(config: dict) -> dict[str, tuple[int, float]]:
    methods = load_methods()
    state = {}
    for root in config["raw_roots"]:
        base = Path(root["path"])
        for directory, _, names in os.walk(base):
            for name in names:
                full = Path(directory) / name
                relative = full.relative_to(base).as_posix()
                if classify(relative, methods)[0] is None:
                    continue
                try:
                    stat = full.stat()
                except OSError:
                    continue
                state[f"{root['id']}:{relative}"] = (stat.st_size, stat.st_mtime)
    return state


def watch(config: dict) -> None:
    chosen = settings(config)
    interval, settle = chosen["check_every_seconds"], 60 * chosen["settle_minutes"]
    _log(config, {"event": "watch_started", "check_every_seconds": interval,
                  "settle_minutes": chosen["settle_minutes"]})
    more = _more_batches(run(config, reason="watch started"))
    last, changed_at = _snapshot(config), None
    started_with = code_version()
    while True:
        time.sleep(interval)
        if code_version() != started_with:
            # New code (e.g. after git pull): exit so the service manager restarts the
            # watcher with it (systemd: Restart=on-failure).
            _log(config, {"event": "watch_restart", "reason": "code changed",
                          "from": started_with, "to": code_version()})
            sys.exit(3)
        current = _snapshot(config)
        if current != last:
            changed_at, last = time.time(), current
        elif changed_at is not None and time.time() - changed_at >= settle:
            changed_at = None
            more = _more_batches(run(config, reason="change detected"))
        elif more:
            more = _more_batches(run(config, reason="next batch"))


def _more_batches(result: dict[str, Any]) -> bool:
    """Run the next batch at the next check? Only after a clean run that made progress."""
    return (result.get("status") == "finished" and result.get("exit_code") == 0
            and bool(result.get("done")) and bool(result.get("remaining")))


# ---- setup -------------------------------------------------------------------------------

def _ask(question: str, default: str) -> str:
    answer = input(f"{question} [{default}]: ").strip()
    return answer or default


def _systemd_units(chosen: dict) -> dict[str, str]:
    labpipe = Path(sys.executable).with_name("labpipe")
    path = os.pathsep.join(dict.fromkeys([str(labpipe.parent), str(Path(chosen["claude"]).parent),
                                          "/usr/local/bin", "/usr/bin", "/bin"]))
    common = (f"WorkingDirectory={PROJECT_ROOT}\nEnvironment=PATH={path}\n"
              "Environment=LABPIPE_AGENT_MODE=unattended\n")
    return {
        "labpipe-watch.service": (
            "[Unit]\nDescription=labpipe: watch the raw data and analyse new measurements\n\n"
            f"[Service]\nType=simple\n{common}ExecStart={labpipe} automation watch\n"
            "Restart=on-failure\nRestartSec=30\n\n[Install]\nWantedBy=default.target\n"),
        "labpipe-nightly.service": (
            "[Unit]\nDescription=labpipe: nightly safety-net pass\n\n"
            f"[Service]\nType=oneshot\n{common}ExecStart={labpipe} automation run --reason nightly\n"),
        "labpipe-nightly.timer": (
            "[Unit]\nDescription=labpipe: nightly safety-net pass\n\n"
            f"[Timer]\nOnCalendar=*-*-* {chosen['nightly_at']}:00\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"),
    }


def setup(config: dict, *, assume_yes: bool = False) -> int:
    if not sys.stdin.isatty():
        print("Run `labpipe automation setup` yourself in a terminal: installing a schedule is "
              "your decision, so agents cannot do it.")
        return 2
    current = settings(config)
    print("labpipe automation: watch the raw data and analyse new files on their own.\n")
    chosen = {
        "check_every_seconds": int(_ask("How often should labpipe check for new files (seconds)?",
                                        str(current["check_every_seconds"]))),
        "settle_minutes": float(_ask("How long must a file stay unchanged before it counts as "
                                     "complete (minutes)?", str(current["settle_minutes"]))),
        "nightly_at": _ask("Time of the nightly safety-net run (HH:MM)?", current["nightly_at"]),
        "max_measurements_per_run": int(_ask("At most how many measurements per AI run "
                                             "(the rest follow in the next run)?",
                                             str(current["max_measurements_per_run"]))),
        "max_budget_usd": float(_ask("Maximum AI cost per run (USD)?",
                                     str(current["max_budget_usd"]))),
        "claude": _ask("Path to the claude command", current["claude"] or shutil.which("claude")
                       or "claude"),
        "notify": {"desktop": _ask("Desktop notifications? (y/n)",
                                   "y" if current["notify"]["desktop"] else "n").lower().startswith("y"),
                   "command": current["notify"].get("command")},
    }
    set_local_block("automation", chosen)
    print("\nSaved to config/local.yaml → automation.")
    if platform.system() != "Linux":
        print("Automatic installation is implemented for Linux (systemd) only so far. On this "
              "system, run `labpipe automation watch` at login and `labpipe automation run` "
              "nightly (launchd on macOS, Task Scheduler on Windows).")
        return 0
    units = _systemd_units(chosen)
    target = Path.home() / ".config" / "systemd" / "user"
    print(f"\nThese files will be written to {target}:")
    for name, text in units.items():
        print(f"\n--- {name}\n{text}")
    if not assume_yes and not _ask("Install and start them now? (y/n)", "n").lower().startswith("y"):
        print("Nothing installed. Rerun `labpipe automation setup` when you are ready.")
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        (target / name).write_text(text, encoding="utf-8")
    for command in (["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", "labpipe-nightly.timer"],
                    ["systemctl", "--user", "enable", "--now", "labpipe-watch.service"]):
        subprocess.run(command, check=True)
        print("ran:", " ".join(command))
    print("\nInstalled. The watcher runs while you are logged in. To keep it running after "
          "logout and after reboots without logging in: `loginctl enable-linger`.\n"
          "Check: `labpipe automation status`. Stop: `systemctl --user disable --now "
          "labpipe-watch.service labpipe-nightly.timer`.")
    return 0


def _services() -> dict[str, str]:
    if platform.system() != "Linux" or not shutil.which("systemctl"):
        return {}
    states = {}
    for unit in ("labpipe-watch.service", "labpipe-nightly.timer"):
        state = subprocess.run(["systemctl", "--user", "is-active", unit],
                               capture_output=True, text=True)
        states[unit] = state.stdout.strip() or state.stderr.strip()
    return states


def _events(config: dict) -> list[dict[str, Any]]:
    log = PROJECT_ROOT / config["logs_dir"] / "automation.log"
    if not log.exists():
        return []
    events = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def health(config: dict, now: float | None = None) -> dict[str, Any]:
    """For doctor: services running, last run succeeded, nothing waiting too long. Reads only."""
    now = time.time() if now is None else now
    problems, fixes = [], []
    services = _services()
    stopped = [unit for unit, state in services.items() if state != "active"]
    if stopped:
        problems.append(f"not running: {', '.join(stopped)}")
        fixes.append("`systemctl --user enable --now " + " ".join(stopped)
                     + "` (or rerun `labpipe automation setup`)")
    runs = [e for e in _events(config)
            if e.get("event") in ("run_finished", "run_failed", "scan_failed")]
    last = runs[-1] if runs else None
    if last is None:
        state = "no automatic run yet"
    elif last["event"] != "run_finished":
        state = f"last run {last['utc']} FAILED: {last.get('reason')}"
        problems.append(state)
        fixes.append("read logs/automation.log; retry with `labpipe automation run`")
    elif last.get("exit_code") != 0:
        state = f"last run {last['utc']} ended with exit code {last.get('exit_code')}"
        problems.append(state)
        fixes.append(f"read the transcript {last.get('transcript')}")
    else:
        state = (f"last run {last['utc']}: {len(last.get('done') or [])} done, "
                 f"{len(last.get('failed') or [])} failed, cost "
                 + (f"{last['cost_usd']:.2f} USD" if isinstance(last.get("cost_usd"), (int, float))
                    else "unknown"))
    waiting = []
    for entry in mf.load(config)["entries"].values():
        if entry.get("status") == "pending" and entry.get("events"):
            since = calendar.timegm(time.strptime(entry["events"][-1]["utc"],
                                                  "%Y-%m-%dT%H:%M:%SZ"))
            if now - since > WAITING_TOO_LONG_HOURS * 3600:
                waiting.append(entry["entry_id"])
    if waiting and services.get("labpipe-watch.service") == "active":
        problems.append(f"{len(waiting)} file(s) pending for more than "
                        f"{WAITING_TOO_LONG_HOURS} h although the watcher runs")
        fixes.append("read logs/automation.log; `labpipe automation run` processes them now")
    others = [p for p in problems if p != state]
    return {"status": "warn" if problems else "ok",
            "message": "; ".join([state, *others]) if problems
                       else f"automatic processing running; {state}",
            "next": " · ".join(fixes) or None,
            "details": {"services": services, "last_run": last, "waiting_long": waiting[:8]}}


def status(config: dict) -> dict[str, Any]:
    return {"settings": settings(config), **_services(), "last_events": _events(config)[-5:]}
