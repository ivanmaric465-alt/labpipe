"""Automatic processing: `labpipe automation watch | run | setup | status`.

* `run`: one pass. Takes a lock (a second run exits at once), scans, and starts
  the AI supervisor only if there is work (pending entries or missing notes),
  for at most `max_measurements_per_run` of them (the batch, passed to the
  agents' `labpipe pending` / `notes todo` as LABPIPE_BATCH). Notifies when
  work is found and when it is done, with what is left, and about anything new
  that needs the user: files no method recognises, files needing a decision,
  failed analyses, analyses out of date (stale). Each is told once; the message
  says what to do, naming the user's own agent tool (config/local.yaml → agents).
* `watch`: checks the raw roots every `check_every_seconds` (file sizes and
  modification times only, no hashing; every file except system files and
  config/ignore.yaml, so unrecognised files count too). After a change, once nothing has
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
import datetime as dt
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
from labpipe.registry import load_methods, skip_reason, user_ignore
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, in_container, set_local_block

DEFAULTS = {"check_every_seconds": 60, "settle_minutes": 3, "nightly_at": "02:00",
            "max_measurements_per_run": 10, "max_budget_usd": 5, "claude": None, "codex": None,
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


NEEDS_YOU = {"unrecognised", "needs_review", "failed", "run_error", "scan_failed"}


def notifications_path(config: dict) -> Path:
    return PROJECT_ROOT / config["logs_dir"] / "notifications.jsonl"


def _last_notification_id(path: Path) -> int:
    """Highest id in the file (0 if none); reads only the tail."""
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            lines = stream.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        try:
            return int(json.loads(line)["id"])
        except (ValueError, KeyError, TypeError):
            continue
    return 0


def _record(config: dict, message: str, kind: str) -> None:
    """Append the notification to logs/notifications.jsonl (read by the browser page)."""
    path = notifications_path(config)
    assert_pipeline_writable(path, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"id": _last_notification_id(path) + 1, "utc": utc_now(),
                                 "kind": kind, "message": message,
                                 "needs_you": kind in NEEDS_YOU}, ensure_ascii=False) + "\n")


def notify(config: dict, message: str, kind: str = "info") -> list[str]:
    """Send `message` through the configured channels; returns the channels that worked.

    `kind` (unrecognised, needs_review, failed, stale, run_error, scan_failed, started,
    finished, test) is kept in logs/notifications.jsonl for the browser page."""
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
    _record(config, message, kind)
    return sent


HARNESS_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def _where() -> str:
    """The repository as the user knows it (in the container: its folder on the host)."""
    if os.environ.get("LABPIPE_HOST_ROOT"):
        return os.environ["LABPIPE_HOST_ROOT"]
    home = Path.home()
    return f"~/{PROJECT_ROOT.relative_to(home).as_posix()}" \
        if PROJECT_ROOT.is_relative_to(home) else str(PROJECT_ROOT)


def _ask_agent(config: dict, request: str) -> str:
    """What to do about a notification, naming the agent tool this machine uses."""
    name = HARNESS_NAMES.get((config.get("agents") or {}).get("harness"))
    if name:
        return f"Open {name} in {_where()} and say: {request}."
    return f"Run `labpipe review` in {_where()} and read logs/review_queue.md."


def _listed(items: list[str], limit: int = 4) -> str:
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _statuses(config: dict) -> dict[str, tuple]:
    return {eid: (e.get("status"), e.get("status_reason"))
            for eid, e in mf.load(config)["entries"].items()}


def _notify_news(config: dict, before: dict[str, tuple],
                 unrecognised: list[str] | None = None) -> dict[str, tuple]:
    """Notify about what newly needs the user since `before`; returns the new state."""
    from labpipe.pipeline import review_queue
    entries = mf.load(config)["entries"]

    def new(status: str) -> list[dict]:
        return sorted((e for eid, e in entries.items() if e.get("status") == status
                       and before.get(eid) != (status, e.get("status_reason"))),
                      key=lambda e: e["relative_path"])

    needs_review, failed, stale = new("needs_review"), new("failed"), new("stale")
    if unrecognised or needs_review or failed or stale:
        review_queue(config)
    if unrecognised:
        notify(config, f"labpipe: {len(unrecognised)} new file(s) not recognised: "
                       f"{_listed(unrecognised)}. " + _ask_agent(config, "check the new files"),
               kind="unrecognised")
    if needs_review:
        notify(config, f"labpipe: {len(needs_review)} file(s) need a decision from you: "
                       + _listed([f"{e['relative_path']} ({e.get('status_reason')})"
                                  for e in needs_review], 3)
                       + ". " + _ask_agent(config, "check the review queue"),
               kind="needs_review")
    if failed:
        notify(config, f"labpipe: {len(failed)} analysis(es) failed: "
                       + _listed([f"{e['relative_path']} ({str(e.get('status_reason'))[:80]})"
                                  for e in failed], 3)
                       + ". " + _ask_agent(config, "check the failed analyses"),
               kind="failed")
    if stale:
        why = {"code updated": sum("current code is" in str(e.get("status_reason"))
                                   for e in stale)}
        why["settings changed"] = len(stale) - why["code updated"]
        notify(config, f"labpipe: {len(stale)} analysis(es) out of date ("
                       + ", ".join(f"{k}: {n}" for k, n in why.items() if n)
                       + f"; e.g. {_listed([e['relative_path'] for e in stale], 2)}). Their "
                       f"results are kept; to redo them run `labpipe requeue --stale` in "
                       f"{_where()} (done in the next run).", kind="stale")
    return {eid: (e.get("status"), e.get("status_reason")) for eid, e in entries.items()}


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
    if done:   # failures are told separately (_notify_news)
        notify(config, f"labpipe: analysed {len(done)} measurement(s) by code alone "
                       f"({_describe(config, done)}); see analysis/report.html",
               kind="finished")
    return done


def _codex_command(config: dict, chosen: dict, last_message: Path) -> list[str]:
    """Codex: the main session acts as the supervisor (its role text is the prompt); the
    analysts are .codex/agents/*.toml. Sandbox workspace-write (raw data outside the repo
    cannot be written), no approvals. --dangerously-bypass-hook-trust makes sure the
    project's labpipe guard hook (.codex/hooks.json) runs even if it was never trusted
    interactively; it also runs any hooks you enabled in ~/.codex yourself.
    Inside the container the container is the sandbox: danger-full-access, which OpenAI
    documents for externally sandboxed environments."""
    from labpipe.agents import codex_trust, load_roles
    inside = in_container()
    codex = "codex" if inside else chosen.get("codex") or shutil.which("codex")
    if not codex:
        raise RuntimeError("the `codex` command was not found; set automation.codex")
    if codex_trust() != "trusted":
        if inside:
            raise RuntimeError("Codex is not logged in or does not trust /labpipe inside the "
                               "container: run `labpipe container login codex`")
        raise RuntimeError(f"Codex does not trust {PROJECT_ROOT} yet, so it would ignore the "
                           "project's rules, guard hook and agents: open the folder once in "
                           "Codex (`codex` there) and trust it")
    agents = config.get("agents") or {}
    supervisor = next(r for r in load_roles() if r["role"] == "supervisor")
    model = (agents.get("models") or {}).get(supervisor["name"]) or (
        agents.get("models") or {}).get("supervisor")
    effort = (agents.get("reasoning") or {}).get("supervisor")
    command = [codex, "exec", "--json", "--ephemeral", "-s",
               "danger-full-access" if inside else "workspace-write",
               "-c", 'approval_policy="never"', "--dangerously-bypass-hook-trust",
               "-o", str(last_message)]
    if model and model != "inherit":
        command += ["-m", model.split("/", 1)[1] if model.startswith("openai/") else model]
    if effort:
        command += ["-c", f'model_reasoning_effort="{effort}"']
    return command + ["You are the labpipe `supervisor` agent. Run one pipeline pass now, "
                      "following these instructions exactly:\n\n" + supervisor["body"]]


def _harness_command(config: dict, chosen: dict, last_message: Path) -> list[str]:
    harness = (config.get("agents") or {}).get("harness")
    if harness == "codex":
        return _codex_command(config, chosen, last_message)
    if harness != "claude-code":
        raise RuntimeError(f"unattended runs are supported with harness claude-code or codex "
                           f"(configured: {harness!r})")
    inside = in_container()   # the container is the sandbox: settings without the sandbox block
    claude = "claude" if inside else chosen.get("claude") or shutil.which("claude")
    if not claude:
        raise RuntimeError("the `claude` command was not found; set automation.claude")
    return [claude, "-p", "Run one pipeline pass.", "--agent", "supervisor",
            "--setting-sources", "project", "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--settings",
            ".claude/unattended-container.json" if inside else ".claude/unattended.json",
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
        before = _statuses(config)
        try:
            scan = scanner.scan(config)
        except RuntimeError as exc:  # e.g. raw root not mounted
            notify(config, f"labpipe: cannot scan the raw data ({exc}); nothing was analysed",
                   kind="scan_failed")
            _log(config, {"event": "scan_failed", "reason": str(exc), "trigger": reason})
            return {"status": "scan_failed", "reason": str(exc)}
        code_only = _analyse_code_only(config, reason)
        before = _notify_news(config, before, scan.get("unrecognised_new"))
        work = _work(config)
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
                       + (f"; {remaining} more queued for the next run" if remaining else ""),
               kind="started")
        stamp = utc_now().replace(":", "").replace("-", "")
        transcript = PROJECT_ROOT / config["logs_dir"] / "runs" / f"auto-{stamp}.jsonl"
        assert_pipeline_writable(transcript, config)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        # `labpipe pending` and `labpipe notes todo` show the agents only this batch.
        env = dict(os.environ, LABPIPE_AGENT_MODE="unattended", LABPIPE_BATCH=",".join(todo_ids))
        env.pop("CLAUDECODE", None)
        env.pop("LABPIPE_RAW_ROOT", None)
        try:
            command = _harness_command(config, chosen, transcript.with_suffix(".last.md"))
            with open(transcript, "w", encoding="utf-8") as out:
                completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env, stdout=out,
                                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           timeout=3 * 3600)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            notify(config, f"labpipe: the AI run could not start or finish ({exc})",
                   kind="run_error")
            _notify_news(config, before)
            _log(config, {"event": "run_failed", "reason": str(exc), "trigger": reason})
            return {"status": "run_failed", "reason": str(exc)}
        result, tokens, errors = {}, {}, []
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    errors.append(line.strip())   # stderr lines interleaved with the JSON stream
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event            # Claude Code: includes total_cost_usd
            elif isinstance(event, dict) and event.get("type") == "turn.completed":
                for key, value in (event.get("usage") or {}).items():   # Codex: tokens only
                    tokens[key] = tokens.get(key, 0) + int(value or 0)
        entries = mf.load(config)["entries"]
        done = [i for i in todo_ids if entries.get(i, {}).get("status") in ("done", "stale")]
        failed = [i for i in todo_ids if entries.get(i, {}).get("status") == "failed"]
        still_missing = {row["entry_id"] for row in notes_todo(config)}
        finished = [i for i in done if i not in still_missing]
        review = [i for i in finished if entries[i].get("review")]
        if completed.returncode != 0 or result.get("is_error"):
            # the last stderr line is the one that stopped it (earlier ones are warnings)
            why = str(result.get("result") or (errors[-1] if errors else "no output"))[:150]
            message = (f"labpipe: the AI run stopped with an error ({why}); "
                       f"{len(todo_ids) - len(finished)} of {len(todo_ids)} measurement(s) still "
                       "waiting. " + _ask_agent(config, "check the last automatic run"))
        else:
            message = (f"labpipe: analysis done, {len(finished)} measurement(s) "
                       f"({_describe(config, finished)})"
                       + (f", {len(review)} to check" if review else "")
                       + (f", {len(still_missing & set(todo_ids))} without notes"
                          if still_missing & set(todo_ids) else "")
                       + "; see analysis/report.html"
                       + (f"; {remaining} left for the next run" if remaining else ""))
        notify(config, message, kind="run_error" if completed.returncode != 0
               or result.get("is_error") else "finished")
        _notify_news(config, before)
        summary = {"event": "run_finished", "trigger": reason, "exit_code": completed.returncode,
                   "transcript": str(transcript.relative_to(PROJECT_ROOT)),
                   "cost_usd": result.get("total_cost_usd"), "done": done, "failed": failed,
                   "remaining": remaining, **({"tokens": tokens} if tokens else {})}
        _log(config, summary)
        return {"status": "finished", **summary}


def _snapshot(config: dict) -> dict[str, tuple[int, float]]:
    ignored = user_ignore()
    state = {}
    for root in config["raw_roots"]:
        base = Path(root["path"])
        for directory, _, names in os.walk(base):
            for name in names:
                full = Path(directory) / name
                relative = full.relative_to(base).as_posix()
                if skip_reason(relative, config, ignored):
                    continue
                try:
                    stat = full.stat()
                except OSError:
                    continue
                state[f"{root['id']}:{relative}"] = (stat.st_size, stat.st_mtime)
    return state


def nightly_due(now: dt.datetime, nightly_at: str, last_nightly: dt.date | None) -> bool:
    """Is the nightly pass due? True from `nightly_at` (HH:MM, local time) on, once per day."""
    hour, minute = (int(part) for part in str(nightly_at).split(":")[:2])
    return last_nightly != now.date() and (now.hour, now.minute) >= (hour, minute)


def _last_nightly(config: dict) -> dt.date | None:
    """Local date of the last nightly pass in logs/automation.log (None if never)."""
    dates = []
    for event in _events(config):
        if event.get("event") == "nightly" or event.get("trigger") == "nightly":
            moment = dt.datetime.strptime(event["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=dt.UTC)
            dates.append(moment.astimezone().date())
    return max(dates, default=None)


def _start_page(config: dict) -> None:
    """In the container: serve the notifications page in a daemon thread (never fatal)."""
    port, token = os.environ.get("LABPIPE_PORT"), os.environ.get("LABPIPE_PAGE_TOKEN")
    if not (in_container() and port and token):
        return
    import threading
    from labpipe import inbox
    try:
        server = inbox.make_server(config, port=int(port), token=token)
    except (OSError, ValueError) as exc:
        _log(config, {"event": "page_failed", "port": port, "reason": str(exc)})
        return
    threading.Thread(target=server.serve_forever, daemon=True, name="labpipe-inbox").start()
    _log(config, {"event": "page_started", "port": server.server_address[1]})


def watch(config: dict) -> None:
    chosen = settings(config)
    interval, settle = chosen["check_every_seconds"], 60 * chosen["settle_minutes"]
    _log(config, {"event": "watch_started", "check_every_seconds": interval,
                  "settle_minutes": chosen["settle_minutes"]})
    _start_page(config)
    more = _more_batches(run(config, reason="watch started"))
    last, changed_at = _snapshot(config), None
    started_with = code_version()
    inside = in_container()   # no systemd timer there: the watcher also does the nightly pass
    last_nightly = _last_nightly(config) if inside else None
    while True:
        time.sleep(interval)
        if code_version() != started_with:
            # New code (e.g. after git pull): exit so the service manager restarts the
            # watcher with it (systemd: Restart=on-failure).
            _log(config, {"event": "watch_restart", "reason": "code changed",
                          "from": started_with, "to": code_version()})
            sys.exit(3)
        if inside and nightly_due(dt.datetime.now(), chosen["nightly_at"], last_nightly):
            last_nightly = dt.date.today()
            _log(config, {"event": "nightly", "trigger": "nightly"})
            more = _more_batches(run(config, reason="nightly"))
            last = _snapshot(config)
            continue
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
    tool = chosen.get("codex") or chosen.get("claude") or "claude"
    path = os.pathsep.join(dict.fromkeys([str(labpipe.parent), str(Path(tool).parent),
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
        "max_budget_usd": float(_ask("Maximum AI cost per run (USD; Claude Code only, Codex "
                                     "has no cost limit)?", str(current["max_budget_usd"]))),
        "claude": current["claude"], "codex": current["codex"],
        "notify": {"desktop": _ask("Desktop notifications? (y/n)",
                                   "y" if current["notify"]["desktop"] else "n").lower().startswith("y"),
                   "command": current["notify"].get("command")},
    }
    harness = (config.get("agents") or {}).get("harness") or "claude-code"
    tool = "codex" if harness == "codex" else "claude"
    chosen[tool] = _ask(f"Path to the {tool} command", current[tool] or shutil.which(tool) or tool)
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
    from labpipe.container import container_mode
    # no systemd services inside the container, nor with container mode on the host
    services = {} if in_container() or container_mode() else _services()
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
                 f"{len(last.get('failed') or [])} failed, "
                 + (f"cost {last['cost_usd']:.2f} USD"
                    if isinstance(last.get("cost_usd"), (int, float))
                    else f"{last['tokens'].get('input_tokens', 0):,} input / "
                         f"{last['tokens'].get('output_tokens', 0):,} output tokens"
                    if last.get("tokens") else "cost unknown"))
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
                       else (f"inside the container; {state}" if in_container()
                             else f"automatic processing running; {state}"),
            "next": " · ".join(fixes) or None,
            "details": {"services": services, "last_run": last, "waiting_long": waiting[:8]}}


def status(config: dict) -> dict[str, Any]:
    return {"settings": settings(config), **_services(), "last_events": _events(config)[-5:]}
