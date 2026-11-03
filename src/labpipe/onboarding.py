"""The first conversation: `labpipe onboarding` (docs/design/install.md → Onboarding).

The agent leads the user through docs/ONBOARDING.md, one step at a time. This module only keeps
the record of which steps are done or skipped (`logs/onboarding.json`) and says which is next, so
the conversation resumes where it stopped. Host side: no numpy. The steps are defined here once;
a test checks that docs/ONBOARDING.md has a section for each.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from labpipe import safety
from labpipe.safety import CODE_ROOT, atomic_write_text

SCHEMA = "labpipe.onboarding/1"
STATE_FILE = "onboarding.json"                    # in the project's logs folder
DOC = "docs/ONBOARDING.md"
# Every step, in order; the tour is several steps so that it can stop and resume anywhere.
STEPS = ("welcome", "files", "methods", "first-results",
         "tour-report", "tour-steering", "tour-agents", "tour-automation",
         "tour-postdoc", "tour-settings", "tour-updates", "done")
TOUR = tuple(step for step in STEPS if step.startswith("tour-")) + ("done",)
NOTES_STEPS = ("first-results", "tour-agents")      # steps whose text depends on who writes notes
_HEADING = re.compile(r"^#{2,3} `([a-z-]+)`", re.M)
_ANY_HEADING = re.compile(r"^#{1,3} ", re.M)


class OnboardingError(Exception):
    pass


def doc_path() -> str:
    """Where the agent reads the steps: in a project, the read-only copy in `.labpipe/`."""
    from labpipe.agents import CODE_COPY
    return DOC if safety.dev_mode() else f"{CODE_COPY}/{DOC}"


def state_file(config: dict | None = None) -> Path:
    logs = (config or {}).get("logs_dir") or "logs"
    return safety.PROJECT_ROOT / logs / STATE_FILE


def load(config: dict | None = None) -> dict[str, dict[str, str]]:
    """step id → {"state": "done" | "skipped", "utc": ...}; unknown ids in the file are ignored."""
    try:
        data = json.loads(state_file(config).read_text(encoding="utf-8"))
        steps = data.get("steps") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {k: v for k, v in steps.items() if k in STEPS and isinstance(v, dict)
            and v.get("state") in ("done", "skipped")}


def _save(steps: dict[str, dict[str, str]], config: dict | None) -> None:
    text = json.dumps({"schema": SCHEMA, "steps": steps}, indent=1, ensure_ascii=False) + "\n"
    atomic_write_text(state_file(config), text, config or safety.load_pipeline_config())


def next_step(steps: dict[str, dict[str, str]]) -> str | None:
    return next((step for step in STEPS if step not in steps), None)


def methods_text() -> str:
    """Per method of the registry (methods.yaml): has it an analyst, does it have notes?
    Generated, so the steps never tell the agent to write notes for a method without them."""
    from labpipe.registry import load_methods
    lines = []
    for name, spec in load_methods().items():
        analyst = spec.get("subagent")
        if spec.get("notes") is False or not analyst:
            lines.append(f"- {name}: analysed by code alone, no notes: show `labpipe {name} "
                         "summary --entry ID` and its checks instead")
        else:
            lines.append(f"- {name}: analyst `{analyst}` writes the notes")
    return "Methods of this installation:\n" + "\n".join(lines)


def section(step: str) -> str:
    """The step's part of docs/ONBOARDING.md (its heading up to the next step's); the steps
    that talk about notes end with the methods list (`methods_text`)."""
    text = (CODE_ROOT / DOC).read_text(encoding="utf-8")
    marks = list(_HEADING.finditer(text))
    for mark in marks:
        if mark.group(1) == step:
            later = _ANY_HEADING.search(text, mark.end())
            body = text[mark.start():later.start() if later else len(text)].strip()
            return body + "\n\n" + methods_text() if step in NOTES_STEPS else body
    return ""


def doc_steps() -> list[str]:
    """The step ids that docs/ONBOARDING.md has sections for."""
    return _HEADING.findall((CODE_ROOT / DOC).read_text(encoding="utf-8"))


def remaining(config: dict | None = None) -> tuple[str, int, int] | None:
    """(next step, handled, total) while steps remain; None when all are done or skipped."""
    steps = load(config)
    upcoming = next_step(steps)
    return None if upcoming is None else (upcoming, len(steps), len(STEPS))


def status(config: dict | None = None) -> dict[str, Any]:
    steps = load(config)
    upcoming = next_step(steps)
    rows = [{"id": step, "state": steps[step]["state"] if step in steps
             else ("next" if step == upcoming else "todo")} for step in STEPS]
    result: dict[str, Any] = {"finished": upcoming is None, "handled": len(steps),
                              "total": len(STEPS), "next": upcoming, "steps": rows}
    if upcoming:
        result["read"] = f"{doc_path()} → {upcoming}"
        result["section"] = section(upcoming)
        result["then"] = f"`labpipe onboarding done {upcoming}` (or `skip {upcoming}`)"
    return result


def _mark(ids: list[str], state: str, config: dict | None) -> dict[str, Any]:
    steps = load(config)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for step in ids:
        steps[step] = {"state": state, "utc": now}
    _save(steps, config)
    return status(config)


def _known(step: str) -> str:
    if step not in STEPS:
        raise OnboardingError(f"unknown step '{step}'; the steps are: {', '.join(STEPS)}")
    return step


def done(step: str, config: dict | None = None) -> dict[str, Any]:
    return _mark([_known(step)], "done", config)


def skip(step: str, config: dict | None = None) -> dict[str, Any]:
    """Skip one step, or `all` of those not handled yet."""
    if step == "all":
        return _mark([s for s in STEPS if s not in load(config)], "skipped", config)
    return _mark([_known(step)], "skipped", config)


def restart(tour: bool = False, config: dict | None = None) -> dict[str, Any]:
    """Forget everything, or only the tour steps (and `done`) so that the tour runs again."""
    steps = load(config)
    steps = {k: v for k, v in steps.items() if k not in TOUR} if tour else {}
    _save(steps, config)
    return status(config)
