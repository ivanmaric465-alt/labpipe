"""The update preview (`labpipe update --preview`): what would a release change in this project's
numbers? By code alone, no AI, no notes; nothing in the project is written.

1. The release's code goes to `$LABPIPE_HOME/releases/<tag>/` (a `git worktree` of the
   installation's clone, kept as a cache and reused).
2. Methods whose analysis version differs between the installed code and the release are the
   only ones that can change; the others are reported "unchanged" without running anything.
3. A scratch project, `$LABPIPE_HOME/preview/<project>-<tag>/`, gets copies of `labpipe.yaml`,
   `config/`, `lab/` and `manifest.json` (no `analysis/`, no `context/`). Its data folders are
   the project's, read-only: in container mode the throwaway container mounts them `:ro`, in
   native mode labpipe's own write checks refuse them.
4. There, with the release's code: `labpipe scan` (marks the changed methods' entries stale),
   `labpipe requeue --stale`, `labpipe analyze --pending --method M [--limit N]`.
5. `labpipe dataset` of the project (running code) is compared with that of the scratch project
   (release code): per method and named quantity the largest absolute change, and whether
   every change is within the uncertainty; measurements that fail now or no longer fail.
   All numbers come from here, not from an agent. The result is `preview.json` in the scratch
   folder.

Container mode builds the release's image from the release folder (`container.build(code=)`)
and runs each command in a throwaway `--rm` container (`container.run_args(command=)`); native
mode (a full install) runs the venv's Python with `PYTHONPATH=<release>/src`.

Host side: Python 3.10, standard library and PyYAML only (numpy lives in the image or the
native venv, and runs in the subprocesses).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from labpipe import container, safety

SCHEMA = "labpipe.preview/1"
COMMIT_FILE = ".labpipe-release-commit"      # in a release folder made without git (archive)
RESULT_STATUSES = ("done", "stale", "missing")
LIST_VERSIONS = (
    "import json\n"
    "from labpipe import versions\n"
    "from labpipe.registry import load_methods\n"
    "methods = load_methods()\n"
    "print(json.dumps({n: versions.describe(n, methods)['analysis_version'] for n in methods}))\n"
)


class PreviewError(RuntimeError):
    pass


def _git(app: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(app), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


def _json_from(text: str) -> Any:
    """The JSON a labpipe command printed (warnings may come before it)."""
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            raise
        return json.loads(text[start:])


# ---- the release's code ------------------------------------------------------------------

def release_folder(app: Path, home: Path, tag: str) -> Path:
    """`$LABPIPE_HOME/releases/<tag>/`: the code of a release, made once and reused."""
    folder = Path(home) / "releases" / tag
    found = _git(app, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
    commit = found.stdout.strip()
    if found.returncode or not commit:
        raise PreviewError(f"{tag} is not a release of this installation")
    if folder.exists():
        have = _git(folder, "rev-parse", "HEAD").stdout.strip() if (folder / ".git").exists() \
            else (folder / COMMIT_FILE).read_text(encoding="utf-8").strip() \
            if (folder / COMMIT_FILE).is_file() else ""
        if have == commit:
            return folder
        raise PreviewError(f"{folder} exists but is not {tag}: remove that folder and run again")
    folder.parent.mkdir(parents=True, exist_ok=True)
    _git(app, "worktree", "prune")
    made = _git(app, "worktree", "add", "--detach", str(folder), tag)
    if made.returncode == 0:
        return folder
    # no worktree (an old git, a read-only .git): the plain files of the release
    archive = subprocess.run(["git", "-C", str(app), "archive", "--format=tar", tag],
                             capture_output=True)
    if archive.returncode != 0:
        raise PreviewError(f"could not get the code of {tag}: {made.stderr.strip()}")
    folder.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        if hasattr(tarfile, "data_filter"):      # (3.12; backported to 3.10.12 and 3.11.4)
            tar.extractall(folder, filter="data")
        else:
            tar.extractall(folder)
    (folder / COMMIT_FILE).write_text(commit + "\n", encoding="utf-8")
    return folder


def analysis_versions(code: Path, project: Path) -> dict[str, str]:
    """Analysis version of every method (shipped and the project's own) of the code folder
    `code`, computed with that code in a subprocess (as scripts/check_release.py does)."""
    env = {**os.environ, "LABPIPE_CODE": str(code), "LABPIPE_PROJECT": str(project),
           "PYTHONPATH": str(Path(code) / "src")}
    env.pop("LABPIPE_ROOT", None)
    run = subprocess.run([sys.executable, "-c", LIST_VERSIONS], cwd=str(project), env=env,
                         capture_output=True, text=True, encoding="utf-8")
    if run.returncode:
        raise PreviewError(f"could not compute the analysis versions with the code in {code}: "
                           + (run.stderr.strip().splitlines() or ["?"])[-1])
    return json.loads(run.stdout.strip().splitlines()[-1])


def changed_methods(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Methods whose calculation differs (or that exist in only one of the two)."""
    return sorted(m for m in set(before) | set(after) if before.get(m) != after.get(m))


# ---- the comparison (pure) ---------------------------------------------------------------

def _measurements(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["entry_id"]: m for block in dataset.get("samples", {}).values()
            for m in block.get("measurements", [])}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compare_method(method: str, before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
                   old_manifest: dict[str, Any], new_manifest: dict[str, Any]) -> dict[str, Any]:
    """Per method: the measurements the scratch project analysed again, and per named quantity
    the largest absolute change (`before` and `after` are the datasets' measurements by entry
    id, see `labpipe dataset`)."""
    rows = {eid: m for eid, m in after.items() if m["method"] == method}
    redone = {eid for eid, m in rows.items() if m["status"] == "done"
              and before.get(eid, {}).get("status") in RESULT_STATUSES}
    now_failing = sorted(eid for eid, m in rows.items() if m["status"] == "failed"
                         and before.get(eid, {}).get("status") != "failed")
    no_longer = sorted(eid for eid, m in rows.items() if m["status"] == "done"
                       and before.get(eid, {}).get("status") == "failed")
    stats: dict[str, dict[str, Any]] = {}
    only_before: set[str] = set()
    only_after: set[str] = set()
    for eid in sorted(redone):
        old_q, new_q = before[eid].get("quantities") or {}, rows[eid].get("quantities") or {}
        only_before |= set(old_q) - set(new_q)
        only_after |= set(new_q) - set(old_q)
        for name in sorted(set(old_q) & set(new_q)):
            old, new = old_q[name], new_q[name]
            if not (_number(old.get("value")) and _number(new.get("value"))):
                continue
            row = stats.setdefault(name, {"n": 0, "unit": new.get("unit") or old.get("unit") or "",
                                          "max_abs_change": 0.0, "changed": 0,
                                          "with_sigma": 0, "beyond_sigma": 0})
            change = abs(new["value"] - old["value"])
            sigma = old.get("sigma") if _number(old.get("sigma")) else new.get("sigma")
            row["n"] += 1
            row["max_abs_change"] = max(row["max_abs_change"], change)
            row["changed"] += change > 0
            if _number(sigma):
                row["with_sigma"] += 1
                row["beyond_sigma"] += change > sigma
    for row in stats.values():
        row["within_sigma"] = None if not row["with_sigma"] else row["beyond_sigma"] == 0
    old_hashes = {e["entry_id"]: e.get("results_sha256") for e in old_manifest.get("entries", {}).values()
                  if e.get("method") == method}
    new_hashes = {e["entry_id"]: e.get("results_sha256") for e in new_manifest.get("entries", {}).values()
                  if e.get("method") == method and e.get("status") == "done"
                  and e["entry_id"] in redone}
    return {"measurements": len(redone), "quantities": stats,
            "only_before": sorted(only_before), "only_after": sorted(only_after),
            "now_failing": now_failing, "no_longer_failing": no_longer,
            "results_identical": sum(old_hashes.get(e) == h for e, h in new_hashes.items()),
            "results_differ": sum(old_hashes.get(e) != h for e, h in new_hashes.items())}


def _short(value: float) -> str:
    return format(value, ".2g")


def describe_method(method: str, info: dict[str, Any]) -> str:
    """One line in plain words, e.g. "xrd: 12 measurements; a.fcc changes up to 0.0004 Å (within σ
    for all); size.fcc changes up to 0.3 nm."."""
    if info["status"] == "unchanged":
        return f"{method}: unchanged (the calculation is the same)."
    if info["status"] == "no-measurements":
        return f"{method}: the calculation changed; this project has no {method} measurements."
    n = info["measurements"]
    if not n:
        return (f"{method}: the calculation changed, but no measurement could be analysed again"
                + (f" ({'; '.join(info['problems'])})" if info.get("problems") else "") + ".")
    parts = [f"{n} measurement{'s' if n != 1 else ''}"
             + (f" (the first {info['limit']} of {info['available']})"
                if info.get("limit") and info["available"] > n else "")]
    prefix = f"{method}."
    for name, row in info["quantities"].items():
        label = name[len(prefix):] if name.startswith(prefix) else name
        if not row["changed"]:
            continue
        text = f"{label} changes up to {_short(row['max_abs_change'])}" + (
            f" {row['unit']}" if row["unit"] else "")
        if row["within_sigma"] is True and row["with_sigma"] == row["n"]:
            text += " (within σ for all)"
        elif row["within_sigma"] is False:
            text += f" (beyond σ for {row['beyond_sigma']} of {row['with_sigma']})"
        elif row["within_sigma"] is None:
            text += " (no σ given)"
        parts.append(text)
    if len(parts) == 1:
        if info["quantities"]:
            parts.append("the named quantities are unchanged")
        elif info["results_differ"]:
            parts.append(f"{info['results_differ']} results.json differ (this method has no "
                         "named quantities to compare)")
        else:
            parts.append("results identical")
    if info["now_failing"]:
        parts.append(f"{len(info['now_failing'])} fail now: " + ", ".join(info["now_failing"][:5]))
    if info["no_longer_failing"]:
        parts.append(f"{len(info['no_longer_failing'])} no longer fail: "
                     + ", ".join(info["no_longer_failing"][:5]))
    if info["only_before"] or info["only_after"]:
        parts.append("quantities that appear or disappear: "
                     + ", ".join([*("-" + q for q in info["only_before"]),
                                  *("+" + q for q in info["only_after"])]))
    return f"{method}: " + "; ".join(parts) + "."


# ---- running ------------------------------------------------------------------------------

class Runner:
    """Runs `labpipe <args>` in the scratch project with the release's code, natively or in a
    throwaway container."""

    def __init__(self, project: Path, scratch: Path, release: Path, say: Callable[[str], None]):
        self.project, self.scratch, self.release, self.say = project, scratch, release, say
        self.in_container = container.local_container_block() is not None
        self.engine = self.tag = self.config = None

    def prepare(self) -> None:
        if self.in_container:
            self.config = container.load_config()
            self.engine = container.current_engine()
            self.tag = container.image_tag(self.config["container"], self.release)
            if container.image_exists(self.engine, self.tag):
                self.say(f"The image for the release is there ({self.tag}).")
            else:
                self.say(f"Building the release's image from {self.release} "
                         "(a few minutes the first time; kept for later) ...")
                container.build(self.engine, self.config["container"], self.release)
            return
        try:
            subprocess.run([sys.executable, "-c", "import numpy, scipy, matplotlib"],
                           check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise PreviewError("this installation has neither a container nor the analysis "
                               "libraries (install.sh --full): there is nothing to run the "
                               "preview with") from exc

    def command(self, args: list[str]) -> list[str]:
        if self.in_container:
            return container.run_args(self.config, self.engine, tag=self.tag,
                                      project=self.scratch, command=args)
        return [sys.executable, "-m", "labpipe.cli", *args]

    def run(self, args: list[str]) -> subprocess.CompletedProcess:
        env = None
        if not self.in_container:
            env = {**os.environ, "LABPIPE_CODE": str(self.release),
                   "LABPIPE_PROJECT": str(self.scratch),
                   "PYTHONPATH": os.pathsep.join(
                       [str(self.release / "src"), *filter(None, [os.environ.get("PYTHONPATH")])])}
            env.pop("LABPIPE_ROOT", None)
        return subprocess.run(self.command(args), cwd=str(self.scratch), env=env,
                              capture_output=True, text=True, encoding="utf-8")


def _run_in_project(project: Path, args: list[str]) -> subprocess.CompletedProcess:
    """`labpipe <args>` of the running code in the project (container mode forwards it)."""
    return subprocess.run([sys.executable, "-m", "labpipe.cli", *args], cwd=str(project),
                          capture_output=True, text=True, encoding="utf-8")


def scratch_folder(home: Path, project: Path, tag: str) -> Path:
    from labpipe.project import slug
    return Path(home) / "preview" / f"{slug(project.name)}-{tag}"


def make_scratch(project: Path, scratch: Path) -> None:
    """A fresh scratch project: labpipe.yaml, config/, lab/ and manifest.json copied (the folder
    is ours and is made again every time)."""
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    shutil.copy2(project / safety.PROJECT_FILE, scratch / safety.PROJECT_FILE)
    shutil.copytree(project / "config", scratch / "config")
    if (project / "lab").is_dir():
        shutil.copytree(project / "lab", scratch / "lab")
    if (project / "manifest.json").is_file():
        shutil.copy2(project / "manifest.json", scratch / "manifest.json")


def _load_manifest(folder: Path) -> dict[str, Any]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"entries": {}}


def _park_pending(scratch: Path) -> int:
    """After the scan, entries that are pending in the scratch project (new files nobody has
    analysed yet) must not take part: they become `baseline` there. Only the stale ones are
    requeued next, so `analyze --pending` redoes exactly the analyses of changed methods."""
    manifest = _load_manifest(scratch)
    parked = 0
    for entry in manifest.get("entries", {}).values():
        if entry.get("status") == "pending":
            entry["status"] = "baseline"
            entry["status_reason"] = "preview: not analysed yet in the project"
            parked += 1
    if parked:
        (scratch / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False)
                                               + "\n", encoding="utf-8")
    return parked


def run(project: Path, app: Path, home: Path, tag: str, from_tag: str, *, limit: int | None,
        say: Callable[[str], None]) -> dict[str, Any]:
    """The whole preview; returns what is written to preview.json (with `summary` lines)."""
    project, home = Path(project), Path(home)
    manifest = _load_manifest(project)
    available = {}
    for entry in manifest.get("entries", {}).values():
        if entry.get("status") in ("done", "stale", "failed"):
            available[entry["method"]] = available.get(entry["method"], 0) + 1
    say(f"Preview of {tag}: your measurements are analysed again with that release's code, "
        "by code only (no AI, no notes), in a scratch copy. Nothing in your project changes.")
    release = release_folder(app, home, tag)
    before = analysis_versions(safety.CODE_ROOT, project)
    after = analysis_versions(release, project)
    changed = changed_methods(before, after)
    result: dict[str, Any] = {
        "schema": SCHEMA, "project": project.as_posix(), "from": from_tag, "to": tag,
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": limit,
        "mode": "container" if container.local_container_block() is not None else "native",
        "release_folder": release.as_posix(), "methods": {}, "problems": []}
    todo = []
    for method in sorted(set(before) | set(after)):
        if not available.get(method):
            if method in changed:
                result["methods"][method] = {"status": "no-measurements"}
            continue
        info: dict[str, Any] = {"analysis_version": {"from": before.get(method),
                                                     "to": after.get(method)}}
        result["methods"][method] = info
        if method in changed:
            todo.append(method)
        else:
            info["status"] = "unchanged"
    if todo:
        scratch = scratch_folder(home, project, tag)
        result["scratch"] = scratch.as_posix()
        say(f"Changed calculation: {', '.join(todo)}. The others are unchanged without running "
            "anything.")
        runner = Runner(project, scratch, release, say)
        runner.prepare()
        say("Reading the current numbers of the project ...")
        old = _run_in_project(project, ["dataset"])
        if old.returncode:
            raise PreviewError("`labpipe dataset` of the project failed (is the container "
                               "running? `labpipe container start`): "
                               + (old.stdout.strip() or old.stderr.strip())[-300:])
        old_dataset = _json_from(old.stdout)
        make_scratch(project, scratch)
        say(f"Scratch project: labpipe scan with {tag} (marks the changed methods' analyses "
            "out of date there) ...")
        for args in (["scan"], None, ["requeue", "--stale"]):
            if args is None:
                _park_pending(scratch)
                continue
            done = runner.run(args)
            if done.returncode:
                result["problems"].append(f"`labpipe {' '.join(args)}` failed: "
                                          + (done.stdout.strip() or done.stderr.strip())[-300:])
        for method in todo:
            say(f"Analysing {method} again with {tag}"
                + (f" (at most {limit} measurements) ..." if limit else " ..."))
            args = ["analyze", "--pending", "--method", method] + (
                ["--limit", str(limit)] if limit else [])
            done = runner.run(args)
            if done.returncode:
                result["methods"][method].setdefault("problems", []).append(
                    "`labpipe analyze` failed: " + (done.stdout.strip() or done.stderr.strip())[-300:])
        say("Reading the numbers of the scratch project ...")
        new = runner.run(["dataset"])
        if new.returncode:
            raise PreviewError("`labpipe dataset` of the scratch project failed: "
                               + (new.stdout.strip() or new.stderr.strip())[-300:])
        old_rows, new_rows = _measurements(old_dataset), _measurements(_json_from(new.stdout))
        new_manifest = _load_manifest(scratch)
        for method in todo:
            info = result["methods"][method]
            info.update(compare_method(method, old_rows, new_rows, manifest, new_manifest))
            info["status"] = "changed"
            info["available"] = available[method]
            info["limit"] = limit
    result["summary"] = [describe_method(m, info) for m, info in result["methods"].items()]
    scratch = Path(result.setdefault("scratch", scratch_folder(home, project, tag).as_posix()))
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "preview.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    result["file"] = (scratch / "preview.json").as_posix()
    return result
