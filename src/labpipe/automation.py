"""Automatic processing: `labpipe automation watch | run | setup | status`.

Automatic processing lives only in the project's container (docs/design/container.md); the
native systemd units and the native macOS/Windows pop-ups are retired.

* `run`: one pass. Takes a lock (a second run exits at once), scans, and starts
  the AI supervisor only if there is work (pending entries or missing notes),
  for at most `max_measurements_per_run` of them (the batch, passed to the
  agents' `labpipe pending` / `notes todo` as LABPIPE_BATCH). Notifies when
  work is found and when it is done, with what is left, and about anything new
  that needs the user: files no method recognises, files needing a decision,
  failed analyses, analyses out of date (stale). Each is told once; the message
  says what to do, naming the user's own agent tool (config/local.yaml → agents).
* `watch`: the container's entry point (outside it, it refuses). Checks the raw roots every `check_every_seconds` (file sizes and
  modification times only, no hashing; every file except system files and
  config/ignore.yaml, so unrecognised files count too). After a change, once nothing has
  changed for `settle_minutes`, it calls `run`. If work is left after a clean
  run that made progress, the next batch runs at the next check.
* `health`: for `labpipe doctor` (last run ok, nothing stuck; read from logs/automation.log).
* `setup`: container mode: asks the timings, writes `automation:` into config/local.yaml,
  offers to restart the container (the watcher reads the settings once, at its start), sets up
  starting the container at login (autostart.py) and offers to remove the old native units.
  Native mode: says that automatic processing needs container mode. Needs a terminal: agents
  cannot install schedules.

Settings (config/local.yaml):

    automation:
      check_every_seconds: 60
      settle_minutes: 3            # also used by `labpipe scan` (else pipeline.yaml's)
      nightly_at: "02:00"          # safety net
      max_measurements_per_run: 10 # the rest follows in the next run
      max_budget_usd: null         # none: the harness's own usage limits apply (subscription);
                                   # set a USD limit per AI run only when paying per use (API key)
      claude: /home/me/.local/bin/claude   # `automation run` by hand on the host (in the container: `claude`)
      notify: {desktop: true, command: null}   # desktop: Linux notify-send (container: needs
                                               # `labpipe container popups on`); command: ["prog", "{message}"]
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

from labpipe import autostart
from labpipe import manifest as mf
from labpipe import scanner
from labpipe.labmethods import source_hash
from labpipe.locking import try_exclusive
from labpipe.notes import todo as notes_todo
from labpipe.provenance import code_version, utc_now
from labpipe.registry import load_methods, skip_reason, user_ignore
from labpipe.safety import (PROJECT_ROOT, SetupError, assert_pipeline_writable, in_container,
                            load_pipeline_config, set_local_block)

DEFAULTS = {"check_every_seconds": 60, "settle_minutes": 3, "nightly_at": "02:00",
            "max_measurements_per_run": 10, "max_budget_usd": None, "claude": None, "codex": None,
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


NEEDS_YOU = {"unrecognised", "needs_review", "to_check", "failed", "run_error", "scan_failed"}


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

    `kind` (unrecognised, needs_review, to_check, failed, stale, run_error, scan_failed, started,
    finished, test) is kept in logs/notifications.jsonl for the browser page."""
    chosen = settings(config)["notify"]
    sent = []
    # Linux only: in the container notify-send reaches the desktop through the D-Bus proxy
    # (`labpipe container popups on`); elsewhere the notifications page is the channel.
    if chosen.get("desktop") and platform.system() == "Linux" and shutil.which("notify-send"):
        command = ["notify-send", "--app-name=labpipe", "labpipe", message]
        try:
            if subprocess.run(command, capture_output=True, timeout=30).returncode == 0:
                sent.append("desktop")
        except (OSError, subprocess.SubprocessError):
            pass
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


def _own_analyses_hint(first: str) -> str:
    """For files that are your own analyses (a figure, a table): the sentence to say, naming
    the folder of the first new file."""
    folder = PurePosixPath(first).parent.as_posix()
    return ((f" Your own analyses (figure, table)? Tell your agent: include my own analyses in "
            f"{folder} (or `labpipe methods import`).")
            if folder != "." else "")


def _listed(items: list[str], limit: int = 4) -> str:
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _statuses(config: dict) -> dict[str, tuple]:
    return {eid: (e.get("status"), e.get("status_reason"))
            for eid, e in mf.load(config)["entries"].items()}


def _stale_reasons(stale: list[dict]) -> dict[str, int]:
    """How many analyses are out of date for which reason (scanner.py words them)."""
    reasons = [str(e.get("status_reason")) for e in stale]
    why = {"calculation changed": sum("calculation changed" in r for r in reasons),
           "code updated": sum("current code is" in r or "before per-method versions" in r
                               for r in reasons)}
    why["settings changed"] = len(stale) - why["calculation changed"] - why["code updated"]
    return why


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
    code_only = {n for n, spec in load_methods().items() if not spec.get("subagent")}
    flagged = [e for e in new("done") if e.get("review") and e["method"] in code_only]
    if unrecognised or needs_review or failed or stale or flagged:
        review_queue(config)
    if unrecognised:
        notify(config, f"labpipe: {len(unrecognised)} new file(s) not recognised: "
                       f"{_listed(unrecognised)}. " + _ask_agent(config, "check the new files")
                       + _own_analyses_hint(unrecognised[0]),
               kind="unrecognised")
    for method in sorted({e["method"] for e in flagged}):   # no analyst reads these: told here
        mine = [e for e in flagged if e["method"] == method]
        notify(config, f"labpipe: {len(mine)} {method.upper()} measurement(s) to check: "
                       + _listed([f"{e['measurement_id']} ({', '.join(e.get('review_codes') or [])})"
                                  for e in mine], 3)
                       + ". " + _ask_agent(config, "check the review queue"), kind="to_check")
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
        why = _stale_reasons(stale)
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


def _analyse_in_process(config: dict, entry_id: str) -> dict[str, Any]:
    """`labpipe <method> analyze --entry ID` in a fresh Python process, so the code of the
    method (also a lab method edited meanwhile) is read now and the version recorded is that of
    the code that ran. Its JSON result; a process that dies or prints no result is recorded as
    failed, with the end of its stderr as the reason."""
    method = mf.load(config)["entries"][entry_id]["method"]
    env = dict(os.environ, LABPIPE_SKIP_REPORT="1")      # the batch builds the report once
    try:
        done = subprocess.run([sys.executable, "-m", "labpipe.cli", method, "analyze",
                               "--entry", entry_id], cwd=PROJECT_ROOT, env=env, text=True,
                              capture_output=True, stdin=subprocess.DEVNULL, timeout=3600)
        result, why = None, f"exit {done.returncode}: " + (done.stderr.strip()[-300:] or "no output")
        try:
            result = json.loads(done.stdout)
        except json.JSONDecodeError:
            pass
    except (OSError, subprocess.SubprocessError) as exc:
        result, why = None, f"{type(exc).__name__}: {exc}"
    if isinstance(result, dict) and result.get("status") in ("done", "failed", "skipped"):
        return result
    reason = f"the analysis process stopped ({why})"
    with mf.locked(config) as data:
        mf.set_status(data["entries"][entry_id], "failed", reason)
    return {"entry_id": entry_id, "status": "failed", "reason": reason}


def _analyse_code_only(config: dict, reason: str) -> list[str]:
    """Methods without an analyst agent (methods.yaml `subagent: null`): analysed by code
    here, so no AI run is started for them. One fresh process per measurement."""
    from labpipe.report import build as build_report
    methods = load_methods()
    code_only = {name for name, spec in methods.items() if not spec.get("subagent")}
    if not code_only:
        return []
    ids = sorted(e["entry_id"] for e in mf.load(config)["entries"].values()
                 if e.get("status") == "pending" and e["method"] in code_only)
    if not ids:
        return []
    results = [_analyse_in_process(config, eid) for eid in ids]
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


def _codex_command(config: dict, chosen: dict, last_message: Path, agent: str = "supervisor",
                   task: str | None = None) -> list[str]:
    """Codex: the main session acts as the supervisor (its role text is the prompt); the
    analysts are .codex/agents/*.toml. `agent`/`task`: another role and its task
    (`labpipe postdoc run`). Sandbox workspace-write (raw data outside the repo
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
    supervisor = next(r for r in load_roles() if agent in (r["name"], r["role"]))
    model = (agents.get("models") or {}).get(supervisor["name"]) or (
        agents.get("models") or {}).get(supervisor["role"])
    effort = (agents.get("reasoning") or {}).get(supervisor["name"]) or (
        agents.get("reasoning") or {}).get(supervisor["role"])
    command = [codex, "exec", "--json", "--ephemeral", "-s",
               "danger-full-access" if inside else "workspace-write",
               "-c", 'approval_policy="never"', "--dangerously-bypass-hook-trust",
               "-o", str(last_message)]
    if model and model != "inherit":
        command += ["-m", model.split("/", 1)[1] if model.startswith("openai/") else model]
    if effort:
        command += ["-c", f'model_reasoning_effort="{effort}"']
    return command + [f"You are the labpipe `{agent}` agent. "
                      + (f"{task} Follow" if task else "Run one pipeline pass now, following")
                      + " these instructions exactly:\n\n" + supervisor["body"]]


def _harness_command(config: dict, chosen: dict, last_message: Path, agent: str = "supervisor",
                     task: str | None = None, budget_usd: float | None = None) -> list[str]:
    harness = (config.get("agents") or {}).get("harness")
    if harness == "codex":
        return _codex_command(config, chosen, last_message, agent, task)
    if harness != "claude-code":
        raise RuntimeError(f"unattended runs are supported with harness claude-code or codex "
                           f"(configured: {harness!r})")
    inside = in_container()   # the container is the sandbox: settings without the sandbox block
    claude = "claude" if inside else chosen.get("claude") or shutil.which("claude")
    if not claude:
        raise RuntimeError("the `claude` command was not found; set automation.claude")
    return [claude, "-p", task or "Run one pipeline pass.", "--agent", agent,
            "--setting-sources", "project", "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--settings",
            ".claude/unattended-container.json" if inside else ".claude/unattended.json",
            "--output-format", "stream-json", "--verbose"] + (
            ["--max-budget-usd", str(budget)]
            if (budget := budget_usd or chosen.get("max_budget_usd")) else [])



def _budget(answer: str) -> float | None:
    return None if answer.strip().lower() in ("", "none", "no", "0") else float(answer)


def _describe(config: dict, ids: list[str]) -> str:
    entries = mf.load(config)["entries"]
    rows = [entries[i] for i in ids if i in entries]
    names = sorted({e.get("sample_id") or e["relative_path"] for e in rows})
    return ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")


def _replot_outdated(config: dict, reason: str) -> tuple[list[str], list[str]]:
    """Redraw the figures whose plot settings changed (code only, no AI). An entry whose
    replot failed is not retried until its plot settings or the code change (`labpipe
    replot --entry ID` retries on request). Returns (redrawn, failed)."""
    from labpipe import pipeline
    from labpipe.provenance import code_version
    from labpipe.report import build as build_report
    current = code_version()
    ids = []
    for e in mf.load(config)["entries"].values():
        if e.get("status") != "done" or not e.get("plot_outdated"):
            continue
        tried = e.get("replot_problem")
        if tried and tried.get("code_version") == current \
                and tried.get("for_plot_sha256") == e.get("plot_sha256_wanted"):
            continue
        ids.append(e["entry_id"])
    if not ids:
        return [], []
    results = [pipeline.replot_entry(config, eid) for eid in sorted(ids)]
    done = [r["entry_id"] for r in results if r["status"] == "done"]
    failed = [r["entry_id"] for r in results if r["status"] == "failed"]
    if done:
        build_report(config)
    _log(config, {"event": "replot_finished", "trigger": reason, "done": done, "failed": failed})
    if failed:
        notify(config, f"labpipe: {len(failed)} plot(s) could not be redrawn because the results "
                       f"would change ({_listed(failed, 2)}); the earlier outputs are kept. "
                       + _ask_agent(config, "check the review queue"), kind="failed")
    return done, failed


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
        replotted, replot_failed = _replot_outdated(config, reason)
        before = _notify_news(config, before, scan.get("unrecognised_new"))
        work = _work(config)
        if not work["pending"] and not work["notes"]:
            if code_only or replotted or replot_failed:
                return {"status": "finished", "exit_code": 0, "done": code_only, "failed": [],
                        "replotted": replotted, "replot_failed": replot_failed,
                        "remaining": 0, "ai": False,
                        "settling": scan["counts"].get("settling", 0)}
            _log(config, {"event": "nothing_to_do", "trigger": reason,
                          "settling": scan["counts"].get("settling", 0)})
            return {"status": "nothing_to_do", "settling": scan["counts"].get("settling", 0)}
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
                   "transcript": transcript.relative_to(PROJECT_ROOT).as_posix(),
                   "cost_usd": result.get("total_cost_usd"), "done": done, "failed": failed,
                   "replotted": replotted, "replot_failed": replot_failed,
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
                tzinfo=dt.timezone.utc)
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
    if not in_container():
        print("labpipe automation watch is the container's own entry point. Automatic "
              "processing runs in the project's container: `labpipe container start` (and "
              "`labpipe automation setup` to start it at login). By hand: `labpipe "
              "automation run`.", file=sys.stderr)
        raise SystemExit(2)
    chosen = settings(config)
    # the scan's settle time (automation.settle_minutes overrides pipeline.yaml in
    # load_pipeline_config), so the watcher never runs a pass the scan still waits out
    interval, settle = chosen["check_every_seconds"], 60 * float(config["settle_minutes"])
    _log(config, {"event": "watch_started", "check_every_seconds": interval,
                  "settle_minutes": config["settle_minutes"]})
    _start_page(config)
    result = run(config, reason="watch started")
    more = _more_batches(result)
    last = _snapshot(config)
    # files still settling: look again once they have had time to settle
    changed_at = time.time() if result.get("settling") else None
    started_with, methods_with = code_version(), source_hash()
    last_nightly = _last_nightly(config)   # the watcher also does the nightly pass
    while True:
        time.sleep(interval)
        if code_version() != started_with:
            # New code: exit so the engine restarts the container (--restart unless-stopped)
            # and the watcher starts with it.
            _log(config, {"event": "watch_restart", "reason": "code changed",
                          "from": started_with, "to": code_version()})
            sys.exit(3)
        if source_hash() != methods_with:
            # Edited lab methods: restart, so the recogniser caches and imports are current.
            _log(config, {"event": "watch_restart", "reason": "lab methods changed"})
            sys.exit(3)
        if nightly_due(dt.datetime.now(), chosen["nightly_at"], last_nightly):
            last_nightly = dt.date.today()
            _log(config, {"event": "nightly", "trigger": "nightly"})
            more = _more_batches(run(config, reason="nightly"))
            last = _snapshot(config)
            continue
        current = _snapshot(config)
        if current != last:
            changed_at, last = time.time(), current
        elif changed_at is not None and time.time() - changed_at >= settle:
            result = run(config, reason="change detected")
            more = _more_batches(result)
            changed_at = time.time() if result.get("settling") else None
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


def _yes(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _setup_timings(config: dict, current: dict) -> None:
    chosen = dict(config.get("automation") or {})   # keeps notify, claude, codex as they are
    chosen.update({
        "check_every_seconds": int(_ask("How often should labpipe check for new files (seconds)?",
                                        str(current["check_every_seconds"]))),
        "settle_minutes": float(_ask("How long must a file stay unchanged before it counts as "
                                     "complete (minutes)?", str(current["settle_minutes"]))),
        "nightly_at": _ask("Time of the nightly safety-net run (HH:MM)?", current["nightly_at"]),
        "max_measurements_per_run": int(_ask("At most how many measurements per AI run "
                                             "(the rest follow in the next run)?",
                                             str(current["max_measurements_per_run"]))),
        "max_budget_usd": _budget(_ask(
            "Cost limit per AI run in USD, only if you pay per use with an API key (Claude Code "
            "only). Enter or 'none': your subscription's own usage limits apply",
            str(current["max_budget_usd"] or "none"))),
    })
    set_local_block("automation", chosen)
    print("\nSaved to config/local.yaml → automation.")


def _offer_restart(assume_yes: bool) -> None:
    """The watcher reads automation: once, at its start: a running container needs a restart."""
    from labpipe import container
    try:
        engine = container.current_engine()
        name = container.container_config(load_pipeline_config())["name"]
        if not container.running(engine, name):
            print("The container is not running: `labpipe container start` starts it with "
                  "these settings.")
            return
    except (container.ContainerError, SetupError):
        return
    print("The running container reads these settings only when it starts.")
    if assume_yes or _yes("Restart it now (labpipe container start --replace)?"):
        container.cmd_start(argparse.Namespace(replace=True, dev=False))
    else:
        print("Later: labpipe container start --replace")


def _old_units(assume_yes: bool) -> None:
    units = autostart.old_units()
    if not units:
        return
    print("\nOld native automation is installed (earlier versions of labpipe). It is retired: "
          "the container does this work now, and both together would analyse twice. Found:")
    for path in units:
        print(f"  {path}")
    print("Will run:")
    for line in autostart.removal_commands(units):
        print(f"  {line}")
    if not (assume_yes or _yes("Remove them now?")):
        print("Not removed. Run the commands above yourself, or rerun `labpipe automation setup`.")
        return
    print("Removed." if autostart.remove_old_units(units) else
          "Files removed, but systemctl reported a problem: check `systemctl --user "
          "list-units 'labpipe-*'`.")


def setup(config: dict, *, assume_yes: bool = False) -> int:
    if not sys.stdin.isatty():
        print("Run `labpipe automation setup` yourself in a terminal: starting things at login "
              "is your decision, so agents cannot do it.")
        return 2
    if in_container():
        print("Run `labpipe automation setup` on the host computer, not inside the container.")
        return 2
    from labpipe import container
    print("labpipe automation: the project's container watches the raw data and analyses new "
          "files on its own.\n")
    _old_units(assume_yes)
    if not container.container_mode():
        print("\nAutomatic processing needs container mode (the native watcher is retired): "
              "`labpipe container setup`, then run this again. Without the container, process "
              "new data by hand: `labpipe automation run`, or tell the agent \"process the "
              "new data\".")
        return 0
    _setup_timings(config, settings(config))
    _offer_restart(assume_yes)
    print("\nStart at login:")
    autostart.offer(container.current_engine(), lambda q: assume_yes or _yes(q))
    print("\nCheck: `labpipe automation status` and `labpipe doctor`.")
    return 0


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
    """For doctor: last run succeeded, nothing waiting too long. Reads only."""
    now = time.time() if now is None else now
    problems, fixes = [], []
    from labpipe.container import container_mode
    watcher = in_container() or container_mode()   # the watcher runs in the container only
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
    if waiting and watcher:
        problems.append(f"{len(waiting)} file(s) pending for more than "
                        f"{WAITING_TOO_LONG_HOURS} h although the watcher runs")
        fixes.append("read logs/automation.log; `labpipe automation run` processes them now")
    others = [p for p in problems if p != state]
    return {"status": "warn" if problems else "ok",
            "message": "; ".join([state, *others]) if problems
                       else (f"inside the container; {state}" if in_container()
                             else f"automatic processing in the container; {state}" if watcher
                             else f"no automatic processing (needs container mode); {state}"),
            "next": " · ".join(fixes) or None,
            "details": {"last_run": last, "waiting_long": waiting[:8]}}


def status(config: dict) -> dict[str, Any]:
    return {"settings": settings(config), "last_events": _events(config)[-5:]}
