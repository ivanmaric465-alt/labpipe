"""labpipe command line.

  labpipe --version                         the version, and the release tag or commit of a git checkout
  labpipe doctor                            setup state of this installation (read-only)
  labpipe init                              create config/local.yaml (this machine's paths)
  labpipe setup [--only STEP]               guided setup in your terminal (data folder, files, sandbox, agents, automation)
  labpipe setup --first [--answers F] [--no-start]   the installer's questions: tools, models, login, first project
  labpipe readonly-view --source PATH       print this OS's commands for a read-only view (runs nothing)
  labpipe inventory                         what labpipe recognises in the data folder(s)
  labpipe methods new NAME --example F --description D   scaffold a new analysis method
  labpipe methods check NAME --example F    run a method on an example and verify its contract
  labpipe methods versions                  analysis and plot version of every method
  labpipe scan [--baseline]                 update manifest.json from the raw roots
  labpipe pending [--json]                  entries waiting for analysis (+ subagent)
  labpipe status [ENTRY] [--json]           manifest summary or one entry
  labpipe <method> analyze --entry ID [--rerun]   analyse one entry (e.g. labpipe xrd analyze)
  labpipe <method> check --entry ID         print the deterministic checks of an entry
  labpipe <method> summary --entry ID       compact human-readable summary (if the method has one)
  labpipe report                            write analysis/report.html (overview for people)
  labpipe onboarding [status|done ID|skip ID|all|restart [--tour]]   the first conversation: where it stands, mark a step
  labpipe migrate [--apply [--yes]]         settings files that use old names: show the change, or apply it (backup first)
  labpipe update [--check|--preview|--to vX.Y.Z] [--yes]   update (or roll back) the installation and this project to a release
  labpipe project new FOLDER [--data PATH]... [--name NAME]   create a project (a folder with labpipe.yaml)
  labpipe project sync                      regenerate AGENTS.md, CLAUDE.md, .labpipe/, harness files
  labpipe agents sync|status                generate the harness's agent files from agents/*.md (+ lab/agents/)
  labpipe roots rename OLD NEW              keep manifest entries when a raw root's id changes
  labpipe automation setup|run|status  automatic processing runs in the container (setup: timings, start at login; needs a terminal)
  labpipe analyze --pending [--limit N]     analyse all pending entries (deterministic loop)
  labpipe requeue (--stale | --baseline | --entry ID)   put entries back to pending
  labpipe review                            write logs/review_queue.md
  labpipe replot [--entry ID | --method M | --all | --outdated]   redraw figures (results are checked, not changed)
  labpipe replay PROVENANCE.json            regenerate outputs from provenance and compare hashes
  labpipe compare [--method M]              (re)draw the comparison figures of samples (analysis/_compare/)
  labpipe xrd refs list|add|build           manage XRD reference phases (CIFs)
  labpipe notes todo                        analysed entries whose notes.md is missing or outdated
  labpipe notes verify --entry ID           check notes.md against results.json (numbers, review codes)
  labpipe notes retire --entry ID [...]     move notes.md to _superseded/ so the analyst rewrites it
  labpipe container setup|build|start|stop|status|logs|login   the container (Podman or Docker)
  labpipe shell [claude|codex]              agent session inside the container
  labpipe guard                             Claude Code PreToolUse hook (event JSON on stdin)
  labpipe context init|index                create context/ from templates / extract the text of context/documents/
  labpipe postdoc start "Q"|verify RUN|list|run "Q" [--budget-usd N]   overall analysis on request

Every command prints JSON (or a short table without --json) and never writes
outside analysis/, logs/ and manifest.json.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

from labpipe import __version__
from labpipe import manifest as mf
from labpipe import container, pipeline, scanner
from labpipe.labmethods import MethodsError
from labpipe.registry import load_methods, shipped_methods
from labpipe import safety
from labpipe.safety import CODE_ROOT, PROJECT_ROOT, SetupError, load_pipeline_config


# Commands that work outside a project (the others explain how to create one).
NO_PROJECT_COMMANDS = {"doctor", "guard", "readonly-view", "methods"}


def _registered_methods() -> dict:
    """The methods to give commands to. A broken lab/methods.yaml must not stop the parser (so
    `doctor` can say what is wrong): the shipped methods only, and the commands that need the
    registry report the error (`main`)."""
    try:
        return load_methods()
    except MethodsError:
        return shipped_methods()


def _refresh_report(config) -> str | None:
    """Rebuild analysis/report.html so it always shows the latest analyses and notes."""
    from labpipe.report import build
    try:
        return build(config)["report"]
    except Exception as exc:  # the report must never make an analysis fail
        return f"report not rebuilt: {type(exc).__name__}: {exc}"


def _print(value, as_json: bool = True) -> None:
    print(json.dumps(value, indent=1, ensure_ascii=False) if as_json else value)


def cmd_init(args, config) -> int:
    """Create the git-ignored files from their examples; never overwrites."""
    created, existing = [], []
    for name in ("local", "samples", "lab"):
        target = PROJECT_ROOT / "config" / f"{name}.yaml"
        if target.exists():
            existing.append(str(target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CODE_ROOT / "config" / f"{name}.example.yaml", target)
        created.append(str(target))
    _print({"created": created, "exists": existing,
            "next": ["`labpipe setup`, or edit config/local.yaml by hand (docs/SAFETY.md)",
                     "run `labpipe doctor`"]})
    return 0


def cmd_doctor(args, config) -> int:
    from labpipe.doctor import run
    result = run()
    _print(result)
    return 0 if result["ready"] else 1


def _batch(rows: list[dict]) -> list[dict]:
    """Inside an automatic run, show the agents only that run's batch (LABPIPE_BATCH)."""
    batch = os.environ.get("LABPIPE_BATCH")
    if batch is None:
        return rows
    chosen = set(filter(None, batch.split(",")))
    return [row for row in rows if row["entry_id"] in chosen]


def cmd_notes_todo(args, config) -> int:
    from labpipe.notes import todo
    _print(_batch(todo(config)))
    return 0


def cmd_methods(args, config) -> int:
    from labpipe import methods_kit
    if args.action == "versions":
        from labpipe.versions import all_versions
        _print(all_versions())
        return 0
    try:
        if args.action == "new":
            _print(methods_kit.new(args.name, Path(args.example), args.description,
                                   analyst=not args.no_analyst))
            return 0
        result = methods_kit.check(args.name, Path(args.example), config)
    except methods_kit.MethodKitError as exc:
        _print({"error": str(exc)})
        return 1
    _print(result)
    return 0 if result["ok"] else 1


def cmd_onboarding(args, config) -> int:
    """The first conversation's record (labpipe.onboarding): status, done, skip, restart."""
    from labpipe import onboarding
    try:
        if args.action == "done":
            result = onboarding.done(args.step, config)
        elif args.action == "skip":
            result = onboarding.skip(args.step, config)
        elif args.action == "restart":
            result = onboarding.restart(args.tour, config)
        else:
            result = onboarding.status(config)
    except onboarding.OnboardingError as exc:
        _print({"error": str(exc)})
        return 2
    _print(result)
    return 0


def cmd_migrate(args, config) -> int:
    """Settings migrations (labpipe.migrations): the plan, or with --apply the change."""
    from datetime import datetime, timezone
    from labpipe import migrations
    planned = migrations.plan(PROJECT_ROOT)
    shown = [{"file": item["file"], "migrations": item["migrations"],
              "comments_kept": item["comments_kept"], "diff": item["diff"]} for item in planned]
    if not planned:
        _print({"pending": 0, "files": [], "message": "the settings files use the current names"})
        return 0
    if not args.apply:
        _print({"pending": len(planned), "files": shown, "applied": False,
                "next": ["show the user the diffs; `labpipe migrate --apply` changes the files "
                         "(a backup is made first) and asks first"]})
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            _print({"error": "`labpipe migrate --apply` asks for confirmation: run it in a "
                    "terminal, or add --yes (after the user agreed)"})
            return 2
        for item in shown:
            print(f"{item['file']}: " + "; ".join(m["description"] for m in item["migrations"])
                  + ("" if item["comments_kept"] else "   (comments in this file are not kept)")
                  + f"\n{item['diff']}\n", file=sys.stderr)
        if input(f"Change {len(planned)} file(s)? A backup is kept. [y/N] ").strip().lower() \
                not in ("y", "yes"):
            _print({"pending": len(planned), "applied": False, "message": "nothing changed"})
            return 1
    backup = PROJECT_ROOT / "backups" / f"migrate-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    try:
        written = migrations.apply(PROJECT_ROOT, planned, backup)
    except migrations.MigrationError as exc:
        _print({"error": str(exc)})
        return 1
    _print({"applied": True, "files": written,
            "backup": backup.relative_to(PROJECT_ROOT).as_posix(),
            "comments_lost": [i["file"] for i in planned if not i["comments_kept"]],
            "next": ["`labpipe doctor`", "results are not changed; settings that mean something "
                     "else now may make entries out of date (`labpipe status`)"]})
    return 0


def cmd_update(args, config) -> int:
    """`labpipe update` (labpipe.update): interactive, runs in the user's terminal."""
    from labpipe import update
    return update.main(args)


def cmd_inventory(args, config) -> int:
    from labpipe.inventory import inventory
    _print(inventory(config))
    return 0


def cmd_notes_verify(args, config) -> int:
    from labpipe.notes import verify_entry
    from labpipe.notes import record_check
    result = verify_entry(config, args.entry)
    if result["ok"] and not result.get("checked_with"):   # (those passed under older rules)
        record_check(config, result)
    if result["ok"]:
        result["report"] = _refresh_report(config)
    _print(result)
    return 0 if result["ok"] else 1


def cmd_setup(args, config) -> int:
    if args.first:
        from labpipe.installer import run as first_run
        return first_run(args.answers, start=not args.no_start)
    if args.answers or args.no_start:
        _print({"error": "--answers and --no-start belong to `labpipe setup --first`"})
        return 2
    from labpipe.wizard import run
    return run(args.only)


def cmd_readonly_view(args, config) -> int:
    from labpipe.roview import commands
    _print(commands(args.source, args.target, args.os))
    return 0


def cmd_agents(args, config) -> int:
    from labpipe import agents, project
    try:
        if args.action == "sync":
            result = project.sync(config=config)     # in a project: everything generated
        else:
            result = agents.status(config)
    except agents.AgentConfigError as exc:
        _print({"error": str(exc)})
        return 1
    _print(result)
    return 1 if result.get("problems") or result.get("error") else 0


def cmd_project(args, config) -> int:
    from labpipe import project
    try:
        result = (project.new(args.folder, args.data, args.name) if args.action == "new"
                  else project.sync(config=config))
    except (project.ProjectError, SetupError) as exc:
        _print({"error": str(exc)})
        return 1
    _print(result)
    return 1 if result.get("problems") or result.get("error") else 0


def cmd_roots_rename(args, config) -> int:
    """Re-key manifest entries when a raw root's id changes (same files, new name)."""
    from labpipe.naming import entry_id
    renamed = []
    with mf.locked(config) as data:
        entries = data["entries"]
        mapping = {eid: entry_id(e["method"], args.new, e["relative_path"])
                   for eid, e in entries.items() if e.get("raw_root") == args.old}
        clash = sorted(set(mapping.values()) & (set(entries) - set(mapping)))
        if clash:
            _print({"error": f"entries already exist under {args.new}: {clash[:5]}"})
            return 1
        for old, new in mapping.items():
            entry = entries.pop(old)
            entry.update({"entry_id": new, "raw_root": args.new, "renamed_from": old})
            entries[new] = entry
            renamed.append({"from": old, "to": new, "relative_path": entry["relative_path"]})
        for entry in entries.values():
            if entry.get("duplicate_of") in mapping:
                entry["duplicate_of"] = mapping[entry["duplicate_of"]]
        for sample, method in {(e.get("sample_id"), e["method"]) for e in entries.values()
                               if e.get("raw_root") == args.new}:
            pipeline.write_index(config, data, sample, method)
    _print({"renamed": len(renamed), "entries": renamed})
    return 0


def cmd_automation(args, config) -> int:
    from labpipe import automation
    if args.action == "setup":
        return automation.setup(config, assume_yes=args.yes)
    if args.action == "watch":
        automation.watch(config)
        return 0
    if args.action == "notify-test":
        _print({"sent": automation.notify(config, "labpipe: test notification")})
        return 0
    result = automation.run(config, reason=args.reason) if args.action == "run" \
        else automation.status(config)
    _print(result)
    return 0 if result.get("status") not in ("scan_failed", "run_failed") else 1


def cmd_report(args, config) -> int:
    from labpipe.report import build
    _print(build(config))
    return 0


def cmd_method_summary(args, config) -> int:
    entry = mf.load(config)["entries"].get(args.entry)
    spec = load_methods()[args.method]
    if not entry or not entry.get("output_dir"):
        _print({"error": f"no analysed output for {args.entry}"})
        return 1
    if not spec.get("summary"):
        _print({"error": f"method {args.method} has no summary module (methods.yaml → summary)"})
        return 1
    results = json.loads((PROJECT_ROOT / entry["output_dir"] / "results.json").read_text(
        encoding="utf-8"))
    _print({"entry_id": args.entry, "output_dir": entry["output_dir"],
            "results_sha256": entry.get("results_sha256"),
            **importlib.import_module(spec["summary"]).summarize(results)})
    return 0


def cmd_notes_retire(args, config) -> int:
    from labpipe.notes import retire
    results = [retire(config, eid) for eid in args.entry]
    _print({"results": results, "report": _refresh_report(config)})
    return 0 if all(r["retired"] for r in results) else 1


def cmd_guard(args, config) -> int:
    from labpipe.guard import main as guard_main
    return guard_main()


def cmd_scan(args, config) -> int:
    summary = scanner.scan(config, baseline=args.baseline)
    summary["review_queue"] = pipeline.review_queue(config)
    _print(summary)
    return 0


def cmd_pending(args, config) -> int:
    data = mf.load(config)
    rows = [{"entry_id": e["entry_id"], "method": e["method"], "subagent": e.get("subagent"),
             "sample_id": e["sample_id"], "relative_path": e["relative_path"]}
            for e in data["entries"].values() if e.get("status") == "pending"]
    rows.sort(key=lambda row: (row["method"], row["relative_path"]))
    _print(_batch(rows))
    return 0


def cmd_status(args, config) -> int:
    data = mf.load(config)
    if args.entry:
        entry = data["entries"].get(args.entry)
        if entry is None:
            _print({"error": f"unknown entry {args.entry}"})
            return 1
        _print(entry)
        return 0
    counts = Counter((e["method"], e["status"]) for e in data["entries"].values())
    _print({"updated_utc": data.get("updated_utc"),
            "counts": {f"{method}/{status}": n for (method, status), n in sorted(counts.items())},
            "plot_out_of_date": sorted(e["entry_id"] for e in data["entries"].values()
                                       if e.get("status") == "done" and e.get("plot_outdated")),
            "replot_problems": sorted(e["entry_id"] for e in data["entries"].values()
                                      if e.get("status") == "done" and e.get("replot_problem")),
            "not_recognised": len(data.get("unrecognised", {}))})
    return 0


def cmd_method_analyze(args, config) -> int:
    data = mf.load(config)
    entry = data["entries"].get(args.entry)
    if entry is None or entry["method"] != args.method:
        _print({"error": f"{args.entry} is not a {args.method} entry"})
        return 1
    result = pipeline.analyze_entry(config, args.entry, rerun=args.rerun)
    result["report"] = _refresh_report(config)
    _print(result)
    return 0 if result["status"] in ("done", "skipped") else 1


def cmd_method_check(args, config) -> int:
    entry = mf.load(config)["entries"].get(args.entry)
    if not entry or not entry.get("output_dir"):
        _print({"error": f"no analysed output for {args.entry}"})
        return 1
    results = json.loads((PROJECT_ROOT / entry["output_dir"] / "results.json").read_text(encoding="utf-8"))
    _print({"entry_id": args.entry, "status": results.get("status"),
            "checks": results.get("checks", [])})
    return 0


def cmd_analyze(args, config) -> int:
    data = mf.load(config)
    pending = sorted((e for e in data["entries"].values() if e.get("status") == "pending"
                      and (not args.method or e["method"] == args.method)),
                     key=lambda e: e["relative_path"])
    if args.limit:
        pending = pending[:args.limit]
    results = [pipeline.analyze_entry(config, entry["entry_id"]) for entry in pending]
    _print({"processed": len(results), "report": _refresh_report(config) if results else None,
            "summary": dict(Counter(r["status"] for r in results)),
            "results": [{k: r.get(k) for k in ("entry_id", "status", "reason", "output_dir", "review")}
                        for r in results]})
    return 0


def cmd_requeue(args, config) -> int:
    changed = []
    with mf.locked(config) as data:
        for entry in data["entries"].values():
            if ((args.stale and entry.get("status") == "stale")
                    or (args.baseline and entry.get("status") == "baseline")
                    or entry["entry_id"] == args.entry):
                if entry.get("status") in ("duplicate", "ignored", "missing", "needs_review"):
                    continue
                mf.set_status(entry, "pending", "requeued by user")
                entry["attempts"] = 0
                changed.append(entry["entry_id"])
    _print({"requeued": changed})
    return 0


def cmd_review(args, config) -> int:
    _print(pipeline.review_queue(config))
    return 0


def cmd_replot(args, config) -> int:
    result = pipeline.replot(config, entry=args.entry, method=args.method,
                             everything=args.all)
    if result.get("replotted"):
        result["report"] = _refresh_report(config)
    _print(result)
    return 1 if result.get("error") or result.get("failed") else 0


def cmd_compare(args, config) -> int:
    from labpipe import compare
    try:
        _print(compare.build(config, method=args.method))
    except ValueError as exc:
        _print({"error": str(exc)})
        return 1
    return 0


def cmd_replay(args, config) -> int:
    from labpipe.replay import replay
    result = replay(Path(args.provenance), config, force=args.force)
    _print(result)
    return 0 if result.get("all_outputs_identical") else 1


# --- dataset (named quantities across samples) ---------------------------------------------

def cmd_dataset(args, config) -> int:
    from labpipe import dataset
    if args.action != "compare":
        _print(dataset.build(config))
        return 0
    try:
        payload = dataset.compare(config, args.quantity, reference=args.reference,
                                  against=args.against, measurements=args.measurement,
                                  each=args.each)
        run = None
        if args.run:
            run = Path(args.run)
            run = (run if run.is_absolute() else PROJECT_ROOT / run).resolve()
            if not run.is_dir() or run.parent != (PROJECT_ROOT / config["analysis_dir"]
                                                   / "_postdoc").resolve():
                raise ValueError(f"--run must be an existing folder in "
                                 f"{config['analysis_dir']}/_postdoc/: {args.run}")
    except ValueError as exc:
        _print({"error": str(exc)})
        return 1
    if run:
        payload["saved_as"] = dataset.save(run, payload, config).relative_to(PROJECT_ROOT).as_posix()
    _print(payload)
    return 0


def _register_dataset(sub) -> None:
    parser = sub.add_parser("dataset", help="all samples, their properties and the named "
                            "quantities of their measurements; `dataset compare` does arithmetic")
    parser.set_defaults(func=cmd_dataset, action=None)
    parser.add_argument("--json", action="store_true", help="JSON output (the default)")
    compare = parser.add_subparsers(dest="action").add_parser(
        "compare", help="difference from a reference sample, or a trend against a quantity")
    compare.add_argument("--quantity", required=True, help="e.g. xrd.a.fcc-1, eds.at.Pt")
    group = compare.add_mutually_exclusive_group(required=True)
    group.add_argument("--reference", help="sample to take differences from")
    group.add_argument("--against", help="quantity (or sample.* property) for the x axis")
    compare.add_argument("--measurement", action="append",
                         help="entry_id or measurement_id to use where a sample has several "
                              "(repeatable)")
    compare.add_argument("--each", action="store_true",
                         help="every measurement of a sample is its own point")
    compare.add_argument("--run", help="postdoc run folder (analysis/_postdoc/<time>): also "
                                       "save the result there as compare-N.json")
# ---- context and postdoc (kept together: one block to merge) ----------------------------

def cmd_context(args, config) -> int:
    from labpipe import context
    result = context.init(config) if args.action == "init" else context.index(config)
    _print(result)
    return 1 if "error" in result else 0


def cmd_postdoc(args, config) -> int:
    from labpipe import postdoc
    try:
        if args.action == "start":
            result = postdoc.start(config, args.question, harness=args.harness, model=args.model)
        elif args.action == "verify":
            result = postdoc.verify(config, args.run)
            if result.get("ok"):
                result["report"] = _refresh_report(config)
        elif args.action == "list":
            result = {"runs": postdoc.list_runs(config)}
        else:
            result = postdoc.run_headless(config, args.question, args.budget_usd)
    except postdoc.PostdocError as exc:
        _print({"error": str(exc)})
        return 1
    _print(result)
    return 0 if result.get("ok", result.get("status") != "failed") else 1


def register_context_postdoc(sub) -> None:
    context = sub.add_parser("context", help="the project's context folder (aims, documents)"
                             ).add_subparsers(dest="action", required=True)
    context.add_parser("init", help="create context/ and its two files from templates (never overwrites)"
                       ).set_defaults(func=cmd_context, needs_config=False)
    context.add_parser("index", help="extract the text of context/documents/ into context/.text/"
                       ).set_defaults(func=cmd_context, needs_config=False)
    postdoc = sub.add_parser("postdoc", help="overall analysis of the whole dataset, on request"
                             ).add_subparsers(dest="action", required=True)
    start = postdoc.add_parser("start", help="create a run (request, inputs, dataset)")
    start.add_argument("question")
    start.add_argument("--harness")
    start.add_argument("--model")
    start.set_defaults(func=cmd_postdoc)
    check = postdoc.add_parser("verify", help="check a run's analysis.md (numbers, review checks, parts)")
    check.add_argument("run")
    check.set_defaults(func=cmd_postdoc)
    postdoc.add_parser("list", help="past runs: state and whether they are up to date"
                       ).set_defaults(func=cmd_postdoc)
    headless = postdoc.add_parser("run", help="start a run and let the agent write it, without a conversation")
    headless.add_argument("question")
    headless.add_argument("--budget-usd", type=float, default=None)
    headless.set_defaults(func=cmd_postdoc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="labpipe", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version=f"labpipe {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor, needs_config=False)
    sub.add_parser("guard").set_defaults(func=cmd_guard, needs_config=False)
    sub.add_parser("init").set_defaults(func=cmd_init, needs_config=False)
    container.register(sub)
    _register_dataset(sub)
    register_context_postdoc(sub)
    setup = sub.add_parser("setup", help="guided setup in your terminal: raw data, sandbox, "
                                         "agents, automation")
    setup.add_argument("--only", choices=["raw-data", "files", "sandbox", "agents", "automation"])
    setup.add_argument("--first", action="store_true",
                       help="the installer's questions: tools, models, login, first project")
    setup.add_argument("--answers", metavar="FILE.yaml",
                       help="with --first: answer from a file instead of asking (CI, tests)")
    setup.add_argument("--no-start", action="store_true",
                       help="with --first: no tool checks, login, build or start")
    setup.set_defaults(func=cmd_setup, needs_config=False)
    roview = sub.add_parser("readonly-view",
                            help="print the commands for a read-only view of the raw data")
    roview.add_argument("--source", required=True, help="the folder holding the raw data")
    roview.add_argument("--target", help="where the view appears (Windows: the share name)")
    roview.add_argument("--os", choices=["linux", "macos", "windows"],
                        help="default: this computer's OS")
    roview.set_defaults(func=cmd_readonly_view, needs_config=False)
    notes = sub.add_parser("notes").add_subparsers(dest="action", required=True)
    notes.add_parser("todo").set_defaults(func=cmd_notes_todo)
    verify = notes.add_parser("verify")
    verify.add_argument("--entry", required=True)
    verify.set_defaults(func=cmd_notes_verify)
    retire = notes.add_parser("retire", help="move notes.md to _superseded/ so it is rewritten")
    retire.add_argument("--entry", action="append", required=True)
    retire.set_defaults(func=cmd_notes_retire)
    scan = sub.add_parser("scan")
    scan.add_argument("--baseline", action="store_true",
                      help="record files seen for the first time as 'baseline' (not analysed)")
    scan.set_defaults(func=cmd_scan)
    onboarding = sub.add_parser("onboarding", help="the first conversation with the agent: where "
                                "it stands, mark a step done or skipped, start again")
    onboarding_actions = onboarding.add_subparsers(dest="action")
    onboarding_actions.add_parser("status", help="the steps and the next one (default)")
    for name, text in (("done", "this step is finished"), ("skip", "skip this step ('all': every "
                                                                  "step not handled yet)")):
        onboarding_actions.add_parser(name, help=text).add_argument("step", metavar="ID")
    onboarding_actions.add_parser("restart", help="forget the record and start again").add_argument(
        "--tour", action="store_true", help="only the tour steps (and done)")
    onboarding.set_defaults(func=cmd_onboarding, needs_config=True, tour=False)
    migrate = sub.add_parser("migrate", help="settings files that use old names: show what would "
                             "change; --apply changes them (backup first)")
    migrate.add_argument("--apply", action="store_true", help="make the change (asks first)")
    migrate.add_argument("--yes", action="store_true", help="with --apply: do not ask")
    migrate.set_defaults(func=cmd_migrate, needs_config=False)
    update = sub.add_parser("update", help="update the installation and this project to a release "
                            "(or go back to an older one); the user runs it, with questions")
    update.add_argument("--check", action="store_true", help="only say what is available")
    update.add_argument("--preview", action="store_true", help="analyse copies of your "
                        "measurements with the new release, by code only, and show what would change")
    update.add_argument("--to", metavar="vX.Y.Z", help="this release instead of the newest "
                        "(an older one = rollback)")
    update.add_argument("--yes", action="store_true", help="no questions (it still prints everything)")
    update.add_argument("--limit", type=int, metavar="N", help="with --preview: at most N "
                        "measurements per method")
    update.add_argument("--resume", metavar="STATE", help=argparse.SUPPRESS)
    update.set_defaults(func=cmd_update, needs_config=False)
    sub.add_parser("inventory", help="what labpipe recognises in the data folder(s)"
                   ).set_defaults(func=cmd_inventory)
    methods_parser = sub.add_parser("methods", help="scaffold a new analysis method, check one, show versions"
                                    ).add_subparsers(dest="action", required=True)
    method_new = methods_parser.add_parser("new", help="create a method from the template")
    method_new.add_argument("name")
    method_new.add_argument("--example", required=True, help="an example input file")
    method_new.add_argument("--description", required=True)
    method_new.add_argument("--no-analyst", action="store_true",
                            help="analysed by code alone: no analyst agent, no notes")
    method_new.set_defaults(func=cmd_methods, needs_config=False)
    method_check = methods_parser.add_parser("check", help="run a method on an example file "
                                             "and verify its contract")
    method_check.add_argument("name")
    method_check.add_argument("--example", required=True)
    method_check.set_defaults(func=cmd_methods)
    methods_parser.add_parser("versions", help="analysis version and plot version of every "
                              "method, with the modules in each").set_defaults(
        func=cmd_methods, needs_config=False)
    sub.add_parser("pending").set_defaults(func=cmd_pending)
    status = sub.add_parser("status")
    status.add_argument("entry", nargs="?")
    status.set_defaults(func=cmd_status)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--pending", action="store_true", required=True)
    analyze.add_argument("--limit", type=int, default=None)
    analyze.add_argument("--method", default=None, help="only entries of this method")
    analyze.set_defaults(func=cmd_analyze)
    requeue = sub.add_parser("requeue")
    group = requeue.add_mutually_exclusive_group(required=True)
    group.add_argument("--stale", action="store_true")
    group.add_argument("--baseline", action="store_true")
    group.add_argument("--entry")
    requeue.set_defaults(func=cmd_requeue)
    sub.add_parser("review").set_defaults(func=cmd_review)
    replot = sub.add_parser("replot", help="redraw figures after a plot setting changed; "
                            "results.json must come out identical (default: --outdated)")
    scope = replot.add_mutually_exclusive_group()
    scope.add_argument("--entry")
    scope.add_argument("--method")
    scope.add_argument("--all", action="store_true", help="every done entry")
    scope.add_argument("--outdated", action="store_true",
                       help="entries whose plot is out of date (the default)")
    replot.set_defaults(func=cmd_replot)
    sub.add_parser("report").set_defaults(func=cmd_report)
    compare = sub.add_parser("compare", help="(re)draw the comparison figures of the samples "
                             "(analysis/_compare/), kept when nothing they depend on changed")
    compare.add_argument("--method", help="only this method's figures")
    compare.set_defaults(func=cmd_compare)
    auto = sub.add_parser("automation").add_subparsers(dest="action", required=True)
    auto_run = auto.add_parser("run", help="one pass: scan; start the AI only if there is work")
    auto_run.add_argument("--reason", default="manual")
    auto_run.set_defaults(func=cmd_automation)
    auto.add_parser("watch", help="the container's entry point (refuses elsewhere)"
                    ).set_defaults(func=cmd_automation)
    auto_setup = auto.add_parser("setup", help="interactive: choose timings, start the container at login, remove old native units")
    auto_setup.add_argument("--yes", action="store_true", help="answer yes to the questions (restart, enable autostart, remove old units)")
    auto_setup.set_defaults(func=cmd_automation)
    auto.add_parser("status").set_defaults(func=cmd_automation)
    auto.add_parser("notify-test").set_defaults(func=cmd_automation)
    roots = sub.add_parser("roots").add_subparsers(dest="action", required=True)
    rename = roots.add_parser("rename", help="re-key manifest entries after a raw root id change")
    rename.add_argument("old")
    rename.add_argument("new")
    rename.set_defaults(func=cmd_roots_rename)
    project_parser = sub.add_parser("project", help="create a project folder; regenerate its "
                                    "generated files").add_subparsers(dest="action", required=True)
    project_new = project_parser.add_parser(
        "new", help="create a project folder (suggestion: ~/labpipe-projects/<name>; not in a "
        "synced folder)")
    project_new.add_argument("folder")
    project_new.add_argument("--data", action="append", default=[], metavar="PATH",
                             help="a data folder (read-only for labpipe); repeat for several")
    project_new.add_argument("--name", default=None, help="the project's name (default: the folder's)")
    project_new.set_defaults(func=cmd_project, needs_config=False)
    project_parser.add_parser("sync", help="regenerate AGENTS.md, CLAUDE.md, .labpipe/ and the "
                              "harness files").set_defaults(func=cmd_project)
    agents_parser = sub.add_parser("agents").add_subparsers(dest="action", required=True)
    agents_parser.add_parser("sync").set_defaults(func=cmd_agents)
    agents_parser.add_parser("status").set_defaults(func=cmd_agents)
    replay = sub.add_parser("replay")
    replay.add_argument("provenance")
    replay.add_argument("--force", action="store_true",
                        help="replay even if the current code commit differs")
    replay.set_defaults(func=cmd_replay)
    for method, spec in _registered_methods().items():
        method_parser = sub.add_parser(method)
        method_sub = method_parser.add_subparsers(dest="action", required=True)
        run = method_sub.add_parser("analyze")
        run.add_argument("--entry", required=True)
        run.add_argument("--rerun", action="store_true",
                         help="also redo done/stale/failed entries (e.g. after steering)")
        run.set_defaults(func=cmd_method_analyze, method=method)
        check = method_sub.add_parser("check")
        check.add_argument("--entry", required=True)
        check.set_defaults(func=cmd_method_check, method=method)
        if spec.get("summary"):
            summary = method_sub.add_parser("summary")
            summary.add_argument("--entry", required=True)
            summary.set_defaults(func=cmd_method_summary, method=method)
        if spec.get("cli_extension"):
            # A method may add its own commands (e.g. `labpipe xrd refs ...`).
            importlib.import_module(spec["cli_extension"]).register(method_sub)
    _add_json_flag(parser)
    return parser


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    """Accept --json on every leaf command (output is JSON anyway)."""
    leaves = []
    stack = [parser]
    while stack:
        current = stack.pop()
        subparsers = [action for action in current._actions
                      if isinstance(action, argparse._SubParsersAction)]
        if not subparsers:
            leaves.append(current)
        for action in subparsers:
            stack.extend(action.choices.values())
    for leaf in leaves:
        if leaf is not parser:
            leaf.add_argument("--json", action="store_true", help="JSON output (the default)")


def cmd_version() -> int:
    """`labpipe --version`: the version, and the release tag (or commit) of a git checkout."""
    from labpipe import release
    info = release.current(CODE_ROOT)
    line = f"labpipe {info['version']}"
    if info["tag"]:
        line += f" ({info['tag']})"
    elif info["commit"]:
        line += f" (commit {info['commit']}, not at a release)"
    if info["dirty"]:
        line += ", with local changes"
    print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    # JSON output contains Å, ±, Δ...: always UTF-8 (Windows consoles default to cp1252).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if (sys.argv[1:] if argv is None else argv) == ["--version"]:
        return cmd_version()
    forwarded = container.forward(sys.argv[1:] if argv is None else argv)
    if forwarded is not None:  # container mode: the command ran inside the container
        return forwarded
    args = build_parser().parse_args(argv)
    if safety.NO_PROJECT and args.command not in NO_PROJECT_COMMANDS and not (
            args.command == "project" and args.action == "new") and not (
            args.command == "setup" and args.first):
        _print({"error": safety.NO_PROJECT_MESSAGE})
        return 2
    try:
        config = load_pipeline_config() if getattr(args, "needs_config", True) else {}
    except SetupError as exc:
        _print({"error": str(exc)})
        return 2
    try:
        result = args.func(args, config)
    except MethodsError as exc:
        _print({"error": str(exc), "next": "fix lab/methods.yaml (docs/ADDING_A_METHOD.md); "
                "`labpipe doctor` shows it too"})
        return 2
    if isinstance(result, int):
        return result
    _print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
