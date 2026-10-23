"""The postdoc: an overall analysis of the whole dataset, on request (docs/design/postdoc.md).

`labpipe postdoc start "Q"` makes analysis/_postdoc/<UTC time>/ with request.md, inputs.json
(sha256 of everything the answer depends on) and dataset.json (`labpipe dataset`). The agent
writes analysis.md there; `labpipe postdoc verify RUN` checks numbers against the sources, not
the prose (a person still reads it):

* every number is in dataset.json, a compare-N.json of the run, or the results.json of a
  measurement the text names (its measurement_id or entry_id);
* a number on a line that cites `documents/<file>` (optionally `p. N`) may instead be
  found in that document's indexed text (that page, else anywhere in it);
* every `review` check code of each named measurement appears in the text;
* the three required parts exist: a "Question" heading holding the question, a "Summary"
  heading and a "Check before using" heading.

Free text without numbers is never restricted. Number matching is the one of
`labpipe notes verify` (labpipe.notes: identifiers like S-9 or RuO2, hkl, ISO dates, list
markers are not numbers; numbers in `inline code` are checked). Also exempt here: the question
text, the run id and timestamps, page numbers after "p."/"pp.", and `documents/...` file names. As with notes, a
number is checked as "found somewhere in the sources", not "used in the right place", and what
the text says about it is not checked.

The rules are versioned (CHECKS_VERSION): verify.json records the version and labpipe's release.
An analysis that passed keeps passing when a later release makes the rules stricter (its text is
not checked again), and the report says "checked with the rules of labpipe X".

Attempts are counted per distinct analysis.md; the third failed attempt records the run as
failed (verify.json lists the reasons) and later calls change nothing.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from labpipe import __version__
from labpipe import manifest as mf
from labpipe.notes import _matches, _walk_numbers, numbers_in_notes
from labpipe.provenance import sha256_file, utc_now
from labpipe.safety import PROJECT_ROOT, assert_pipeline_writable, atomic_write_text

CHECKS_VERSION = 2      # bump when the rules in `_check` get stricter (like notes.CHECKS_VERSION)
RUN_ID = re.compile(r"^\d{8}T\d{6}Z(?:-\d+)?$")
MAX_ATTEMPTS = 3
PARTS = {"Question": r"question", "Summary": r"summary", "Check before using": r"check before using"}
_MASK = [
    re.compile(r"\bpp?\.\s*\d+(?:\s*[-–]\s*\d+)?"),          # page numbers
    re.compile(r"documents/[^\s`)\],;]+"),                    # cited file names
    re.compile(r"\b\d{8}T\d{6}Z(?:-\d+)?\b"),                 # run ids
]
_DOC_REF = re.compile(r"documents/([^\s`)\],;]+)")
_PAGE_MARK = re.compile(r"^=== page (\d+) ===$", re.M)


class PostdocError(RuntimeError):
    pass


# ---------------------------------------------------------------- files

def runs_dir(config: dict) -> Path:
    return PROJECT_ROOT / config["analysis_dir"] / "_postdoc"


def run_path(config: dict, run: str) -> Path:
    """A run by its id (or by the path of its folder)."""
    path = runs_dir(config) / Path(str(run).rstrip("/")).name
    if not RUN_ID.match(path.name) or not path.is_dir():
        raise PostdocError(f"no postdoc run {run!r} (see `labpipe postdoc list`)")
    return path


def _rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _write_json(config: dict, path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n", config)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _request(path: Path) -> dict[str, Any]:
    try:
        _, front, body = (path / "request.md").read_text(encoding="utf-8").split("---", 2)
        return {**(yaml.safe_load(front) or {}), "body": body.strip()}
    except (OSError, ValueError):
        return {"body": ""}


def _dataset(config: dict) -> dict[str, Any]:
    try:
        module = importlib.import_module("labpipe.dataset")
    except ImportError as exc:
        raise PostdocError("labpipe.dataset is not available in this installation") from exc
    return module.build(config)


# ---------------------------------------------------------------- inputs and freshness

def _measurement_files(config: dict) -> dict[str, dict[str, Any]]:
    """entry_id -> results.json hash and notes.md hash of every analysed entry."""
    found = {}
    for eid, entry in sorted(mf.load(config)["entries"].items()):
        if entry.get("status") not in ("done", "stale") or not entry.get("output_dir"):
            continue
        out = PROJECT_ROOT / entry["output_dir"]
        if not (out / "results.json").exists():
            continue
        notes = out / "notes.md"
        found[eid] = {"sample_id": entry.get("sample_id"), "method": entry.get("method"),
                      "results_sha256": sha256_file(out / "results.json"),
                      "notes_sha256": sha256_file(notes) if notes.exists() else None,
                      "same_analysis_sha256": entry.get("same_analysis_sha256", [])}
    return found


def _tree(paths: list[Path]) -> dict[str, str]:
    return {_rel(p): sha256_file(p) for p in paths if p.is_file()}


def current_inputs(config: dict) -> dict[str, Any]:
    context = PROJECT_ROOT / "context"
    documents = sorted(p for p in (context / "documents").rglob("*") if p.is_file()
                       and not any(part.startswith(".") for part in
                                   p.relative_to(context / "documents").parts))
    settings = [PROJECT_ROOT / "config" / "samples.yaml",
                *sorted((PROJECT_ROOT / "config" / "measurements").rglob("*"))]
    return {"labpipe_version": __version__,
            "context": _tree([context / "context.md", context / "instructions.md"]),
            "documents": _tree(documents),
            "settings": _tree(settings),
            "measurements": _measurement_files(config)}


def _changed(old: dict[str, str], new: dict[str, str]) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def _compare(recorded: dict[str, Any], now: dict[str, Any]) -> dict[str, Any]:
    before, after = recorded.get("measurements", {}), now["measurements"]

    def same(e: str) -> bool:   # equal, or differing only in the list of figures (replot)
        return before[e]["results_sha256"] in (after[e]["results_sha256"],
                                                *after[e].get("same_analysis_sha256", []))
    changed = sorted(e for e in set(before) & set(after) if not same(e))
    notes = sorted(e for e in set(before) & set(after) if same(e)
                   and before[e].get("notes_sha256") != after[e].get("notes_sha256"))
    new = [{"entry_id": e, "sample_id": after[e]["sample_id"], "method": after[e]["method"]}
           for e in sorted(set(after) - set(before))]
    removed = [{"entry_id": e, "sample_id": before[e].get("sample_id"),
                "method": before[e].get("method")} for e in sorted(set(before) - set(after))]
    context = _changed({**recorded.get("context", {}), **recorded.get("documents", {})},
                       {**now["context"], **now["documents"]})
    settings = _changed(recorded.get("settings", {}), now["settings"])
    return {"up_to_date": not (changed or notes or new or removed or context or settings),
            "changed_results": changed, "changed_notes": notes, "new_measurements": new,
            "removed": removed, "changed_context": context, "changed_settings": settings}


def freshness(config: dict, run: str, now: dict[str, Any] | None = None) -> dict[str, Any]:
    path = run_path(config, run)
    recorded = _read_json(path / "inputs.json")
    if recorded is None:
        raise PostdocError(f"{_rel(path)}/inputs.json is missing or unreadable")
    return _compare(recorded, now or current_inputs(config))


# ---------------------------------------------------------------- start

def start(config: dict, question: str, *, harness: str | None = None,
          model: str | None = None) -> dict[str, Any]:
    question = question.strip()
    if not question:
        raise PostdocError("the question is empty")
    agents = config.get("agents") or {}
    harness = harness or agents.get("harness")
    model = model or (agents.get("models") or {}).get("postdoc") or "inherit"
    base = runs_dir(config)
    stamp = utc_now().replace("-", "").replace(":", "")
    run, n = stamp, 1
    while (base / run).exists():
        n += 1
        run = f"{stamp}-{n}"
    path = base / run
    dataset = _dataset(config)
    front = yaml.safe_dump({"question": question, "utc": utc_now(), "harness": harness,
                            "model": model}, sort_keys=False, allow_unicode=True, width=1000)
    atomic_write_text(path / "request.md", f"---\n{front}---\n\n{question}\n", config)
    _write_json(config, path / "inputs.json", current_inputs(config))
    _write_json(config, path / "dataset.json", dataset)
    samples = dataset.get("samples", {})
    return {"run": run, "path": _rel(path), "question": question, "harness": harness,
            "model": model, "samples": len(samples),
            "measurements": sum(len(s.get("measurements", [])) for s in samples.values()),
            "next": [f"the postdoc writes {_rel(path)}/analysis.md",
                     f"then `labpipe postdoc verify {run}`"]}


# ---------------------------------------------------------------- verify

def _mask(text: str, patterns: list[re.Pattern]) -> str:
    for pattern in patterns:
        text = pattern.sub(lambda m: " " * len(m.group()), text)
    return text


def _named(text: str, value: str | None) -> bool:
    return bool(value) and re.search(rf"(?<![\w.\-]){re.escape(value)}(?![\w\-])", text) is not None


def _review_codes(measurement: dict, results: Any) -> set[str]:
    codes = set(measurement.get("review_codes") or [])
    if isinstance(results, dict):
        codes |= {c["code"] for c in results.get("checks", []) if c.get("severity") == "review"}
    return codes


def _document_numbers(config: dict, name: str, page: int | None,
                      cache: dict) -> tuple[list[float] | None, str | None]:
    """Numbers in a document's indexed text (one page if given and the text has pages)."""
    from labpipe import context
    key = (name, page)
    if key in cache:
        return cache[key]
    row = context.load_index().get("documents", {}).get(name)
    if row is None:
        result = (None, f"documents/{name} is not in context/documents/ (run `labpipe context index`)")
    elif row.get("status") != "indexed":
        result = (None, f"documents/{name} is not indexed ({row.get('reason')})")
    else:
        text = (PROJECT_ROOT / row["text"]).read_text(encoding="utf-8")
        if page is not None and _PAGE_MARK.search(text):
            parts = _PAGE_MARK.split(text)   # ["", "1", text1, "2", text2, ...]
            pages = dict(zip(parts[1::2], parts[2::2]))
            text = pages.get(str(page))
            if text is None:
                cache[key] = (None, f"documents/{name} has no page {page}")
                return cache[key]
        result = ([n for n, _ in _walk_numbers(text)], None)
    cache[key] = result
    return result


def _check(config: dict, path: Path, text: str) -> dict[str, Any]:
    dataset = _read_json(path / "dataset.json") or {}
    question = _request(path)["body"]
    known: list[tuple[float, str]] = list(_walk_numbers(dataset, "dataset.json"))
    for compare in sorted(path.glob("compare-*.json")):
        known += list(_walk_numbers(_read_json(compare), compare.name))
    named, warnings, codes_missing = [], [], {}
    for sample in (dataset.get("samples") or {}).values():
        for measurement in sample.get("measurements", []):
            if not (_named(text, measurement.get("entry_id"))
                    or _named(text, measurement.get("measurement_id"))):
                continue
            eid = measurement.get("entry_id") or measurement.get("measurement_id")
            named.append(eid)
            results = None
            if measurement.get("output_dir"):
                results = _read_json(PROJECT_ROOT / measurement["output_dir"] / "results.json")
                if results is None:
                    warnings.append(f"results.json of {eid} could not be read")
            known += list(_walk_numbers(results, f"{eid}/results.json"))
            missing = sorted(c for c in _review_codes(measurement, results) if c not in text)
            if missing:
                codes_missing[eid] = missing
    masks = list(_MASK)
    if question:
        masks.insert(0, re.compile(re.escape(question)))
    masked = _mask(text, masks)
    lines, cache, unmatched, matched = text.splitlines(), {}, [], 0
    for item in numbers_in_notes(masked):
        shown = item["text"].replace("−", "-")
        value = float(shown)
        line = lines[item["line"] - 1]
        item = {**item, "context": line.strip()[:160]}
        if any(_matches(shown, value, number) for number, _ in known):
            matched += 1
            continue
        cited = [m.rstrip(".:") for m in _DOC_REF.findall(line)]
        page = re.search(r"\bpp?\.\s*(\d+)", line)
        reasons = []
        for name in cited:
            numbers, why = _document_numbers(config, name, int(page.group(1)) if page else None,
                                             cache)
            if numbers is not None and any(_matches(shown, value, n) for n in numbers):
                break
            reasons.append(why or f"not in documents/{name}" + (f" p. {page.group(1)}" if page else ""))
        else:
            unmatched.append({**item, "reason": "; ".join(reasons) if reasons else
                              "not in dataset.json, compare-N.json or the named measurements' "
                              "results.json"})
            continue
        matched += 1
    headings = [(m.start(), m.group(1).strip()) for m in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text)]
    missing_parts = []
    for part, pattern in PARTS.items():
        found = [i for i, (_, title) in enumerate(headings)
                 if re.match(rf"(?:\d+[.)]\s*)?{pattern}\b", title, re.I)]
        if not found:
            missing_parts.append(part)
        elif part == "Question":
            start = headings[found[0]][0]
            end = next((pos for pos, _ in headings if pos > start), len(text))
            if " ".join(question.split()).casefold() not in " ".join(text[start:end].split()).casefold():
                missing_parts.append("Question (the heading must contain the question text)")
    problems = []
    if unmatched:
        problems.append(f"{len(unmatched)} number(s) not found in the sources")
    for eid, codes in codes_missing.items():
        problems.append(f"review checks of {eid} not mentioned: {', '.join(codes)}")
    if missing_parts:
        problems.append(f"required part(s) missing: {', '.join(missing_parts)}")
    return {"problems": problems, "unmatched": unmatched, "numbers_checked": matched + len(unmatched),
            "named_measurements": sorted(named), "missing_review_codes": codes_missing,
            "missing_parts": missing_parts, "warnings": warnings}


def verify(config: dict, run: str) -> dict[str, Any]:
    path = run_path(config, run)
    analysis = path / "analysis.md"
    if not analysis.exists():
        return {"run": path.name, "ok": False, "state": "no analysis",
                "problems": [f"{_rel(analysis)} does not exist"]}
    previous = _read_json(path / "verify.json") or {}
    sha = sha256_file(analysis)
    if previous.get("state") == "failed" or previous.get("analysis_sha256") == sha:
        return {**previous, "unchanged": True}   # failed stays failed; same text, same answer
    attempts = int(previous.get("attempts", 0)) + 1
    checked = _check(config, path, analysis.read_text(encoding="utf-8"))
    ok = not checked["problems"]
    state = "verified" if ok else "failed" if attempts >= MAX_ATTEMPTS else "retry"
    result = {"schema": "labpipe.postdoc.verify/1", "run": path.name, "utc": utc_now(), "ok": ok,
              "state": state, "rules": CHECKS_VERSION, "labpipe": __version__,
              "attempts": attempts, "max_attempts": MAX_ATTEMPTS,
              "analysis_sha256": sha, **checked,
              "history": [*previous.get("history", []),
                          {"attempt": attempts, "utc": utc_now(), "ok": ok,
                           "problems": checked["problems"]}]}
    if state == "retry":
        result["next"] = f"fix analysis.md and run `labpipe postdoc verify {path.name}` again " \
                         f"({MAX_ATTEMPTS - attempts} attempt(s) left)"
    if state == "failed":
        result["next"] = "the run is recorded as failed; ask the postdoc again for a new run"
    _write_json(config, path / "verify.json", result)
    return result


# ---------------------------------------------------------------- listing

def _state(path: Path) -> str:
    analysis = path / "analysis.md"
    if not analysis.exists():
        return "no analysis"
    recorded = _read_json(path / "verify.json") or {}
    if recorded.get("state") == "failed":
        return "failed"
    if recorded.get("ok") and recorded.get("analysis_sha256") == sha256_file(analysis):
        return "verified"
    return "unverified"


def _rules(path: Path) -> dict[str, Any]:
    """The rules a run's analysis passed (verify.json before the versions were recorded: 1)."""
    recorded = _read_json(path / "verify.json") or {}
    rules = recorded.get("rules", 1)
    return {"rules": rules, "labpipe": recorded.get("labpipe"),
            "older_rules": bool(recorded.get("ok")) and rules < CHECKS_VERSION}


def list_runs(config: dict) -> list[dict[str, Any]]:
    base = runs_dir(config)
    if not base.is_dir():
        return []
    now = current_inputs(config)
    rows = []
    for path in sorted((p for p in base.iterdir() if p.is_dir() and RUN_ID.match(p.name)),
                       reverse=True):
        request = _request(path)
        recorded = _read_json(path / "inputs.json")
        fresh = _compare(recorded, now) if recorded else None
        rows.append({"run": path.name, "path": _rel(path), "question": request.get("question"),
                     "utc": request.get("utc"), "model": request.get("model"),
                     "harness": request.get("harness"), "state": _state(path),
                     **_rules(path),
                     "freshness": None if fresh is None else
                     {k: (v if isinstance(v, bool) else len(v)) for k, v in fresh.items()}})
    return rows


def latest(config: dict) -> dict[str, Any]:
    """The newest verified run, and the newest run when it is newer and failed or unverified."""
    rows = list_runs(config)
    verified = next((r for r in rows if r["state"] == "verified"), None)
    newest = rows[0] if rows else None
    newer = newest if newest and newest is not verified and newest["state"] in (
        "failed", "unverified") else None
    return {"verified": verified, "newer": newer}


# ---------------------------------------------------------------- the non-conversation way

def _prompt(run: str) -> str:
    return (f"Act as the postdoc for run {run}. The question is in "
            f"analysis/_postdoc/{run}/request.md. Follow your role instructions: write "
            f"analysis/_postdoc/{run}/analysis.md, then run `labpipe postdoc verify {run}` "
            f"and fix it at most twice. Do nothing else.")


def run_headless(config: dict, question: str, budget_usd: float | None = None) -> dict[str, Any]:
    """`labpipe postdoc run`: start a run and let the configured harness write it, the way
    automatic runs start the supervisor (same command builder, same container handling; in
    container mode the CLI forwards this command into the container). Never started by
    automatic runs. Usage counts against the harness's own limits (the subscription); a USD
    limit (`--budget-usd`, else automation.max_budget_usd) applies only when given, and only
    to Claude Code (Codex has no cost flag)."""
    from labpipe import automation
    chosen = automation.settings(config)
    info = start(config, question)
    run = info["run"]
    path = PROJECT_ROOT / info["path"]
    transcript = PROJECT_ROOT / config["logs_dir"] / "runs" / f"postdoc-{run}.jsonl"
    assert_pipeline_writable(transcript, config)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    automation.notify(config, f"labpipe: the postdoc started on: {question[:80]}", kind="started")
    env = dict(os.environ, LABPIPE_AGENT_MODE="postdoc")
    env.pop("CLAUDECODE", None)
    env.pop("LABPIPE_RAW_ROOT", None)
    failed = ""
    result, completed = {}, None
    try:
        command = automation._harness_command(config, chosen, transcript.with_suffix(".last.md"),
                                               agent="postdoc", task=_prompt(run),
                                               budget_usd=budget_usd)
        with open(transcript, "w", encoding="utf-8") as out:
            completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env, stdout=out,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       timeout=3 * 3600)
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
        if completed.returncode != 0 or result.get("is_error"):
            failed = f"the AI run stopped with an error ({str(result.get('result') or 'no output')[:120]})"
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        failed = f"the AI run could not start or finish ({exc})"
    state = _state(path)
    reason = failed
    if not failed and state == "unverified":      # the agent did not run verify itself
        outcome = verify(config, run)
        state = _state(path)
        reason = "; ".join(outcome.get("problems", []))
    elif not failed and state == "no analysis":
        reason = "the postdoc wrote no analysis.md"
    elif not failed and state == "failed":
        reason = "; ".join((_read_json(path / "verify.json") or {}).get("problems", []))
    try:
        from labpipe.report import build
        build(config)
    except Exception:   # the report must never make the run fail
        pass
    if state == "verified" and not failed:
        automation.notify(config, f"labpipe: the postdoc's analysis is ready ({question[:60]}); "
                                  f"see analysis/report.html in {automation._where()}",
                          kind="finished")
    else:
        automation.notify(
            config, f"labpipe: the postdoc's analysis did not finish ({reason or state}). "
                    + automation._ask_agent(config, f"ask the postdoc: {question}"),
            kind="run_error")
    return {"status": "finished" if state == "verified" else "failed", "run": run,
            "path": info["path"], "state": state, "reason": reason or None,
            "cost_usd": result.get("total_cost_usd"),
            "transcript": transcript.relative_to(PROJECT_ROOT).as_posix()}
