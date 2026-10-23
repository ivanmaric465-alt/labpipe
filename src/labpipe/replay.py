"""Regenerate an analysis from its provenance.json alone and compare output hashes.

Uses: the input path + sha256, the resolved settings (stored in full), the code that made it
and the reference files (checked by sha256). The code must match: the method's analysis and
plot version (records made since per-method versions), else the recorded commit, clean
(older records). A different commit with equal versions is only noted. --force compares
anyway. Output goes to logs/replay/<stamp>/. Every recorded output is compared (thumb.jpg too).
An input that is no longer at its recorded place is looked for in the manifest by its sha256
(a moved file: "input moved: found at <path>" in `notes`). A different python, library, requirements.lock or runtime is a warning in `notes`,
not a refusal. A comparison figure (analysis/_compare/) is redrawn from the data recorded in its
provenance (`labpipe.compare.replay`).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from labpipe import labmethods
from labpipe.provenance import git_info, sha256_file, utc_now
from labpipe.safety import CODE_ROOT, PROJECT_ROOT


# Files that a method run does not regenerate (provenance.json is the record itself).
_NOT_REGENERATED = {"provenance.json", "notes.md"}


def _compared(record: dict) -> list[str]:
    """Every output the record lists (results, figures, thumb.jpg, ...)."""
    return [name for name in record["outputs"] if name not in _NOT_REGENERATED]


def environment_notes(record: dict) -> list[str]:
    """Warnings where the recorded environment (python, libraries, requirements.lock, native or
    container) differs from this one. Never a refusal: the outputs may still be identical."""
    from labpipe.provenance import environment, runtime
    notes = []
    then, now = record.get("environment") or {}, environment()
    if then.get("python") and then["python"] != now["python"]:
        notes.append(f"recorded python {then['python']}, now {now['python']}: outputs may differ")
    for name, old in sorted((then.get("libraries") or {}).items()):
        new = now["libraries"].get(name)
        if old != new:
            notes.append(f"recorded {name} {old or 'not installed'}, now "
                         f"{new or 'not installed'}: outputs may differ")
    if then.get("requirements_lock_sha256") and \
            then["requirements_lock_sha256"] != now["requirements_lock_sha256"]:
        notes.append("requirements.lock differs from the recorded one: outputs may differ")
    mode = (record.get("runtime") or {}).get("mode")
    if mode and mode != runtime()["mode"]:
        notes.append(f"recorded run was {mode}, now {runtime()['mode']}: outputs may differ")
    return notes


def _input_path(recorded: dict, config: dict) -> Path:
    """The recorded path, or (if it does not exist here, e.g. a host path inside the
    container or the reverse) the same file under the current raw root of that id."""
    path = Path(recorded["path"])
    if path.exists():
        return path
    for root in config.get("raw_roots", []):
        if root["id"] == recorded.get("raw_root") and recorded.get("relative_path"):
            return Path(root["path"]) / recorded["relative_path"]
    return path


def _moved_input(recorded: dict, method: str, config: dict) -> tuple[Path, str] | None:
    """The input at its new place: a file of the manifest (not missing) of the same method
    whose sha256 is the recorded one. (path, its relative path), or None. For a file that was
    moved in the data folder after the analysis, which provenance.json still names at the old
    place."""
    from labpipe import manifest as mf
    try:
        entries = mf.load(config)["entries"].values()
    except (OSError, ValueError, RuntimeError):
        return None
    for entry in sorted(entries, key=lambda e: e["entry_id"]):
        if (entry.get("sha256") == recorded.get("sha256") and entry.get("method") == method
                and entry.get("status") not in ("missing", "duplicate", "ignored")):
            for root in config.get("raw_roots", []):
                path = Path(root["path"]) / entry["relative_path"]
                if root["id"] == entry["raw_root"] and path.is_file():
                    return path, entry["relative_path"]
    return None


def _check_outside(record: dict, provenance_path: Path, config: dict) -> dict[str, Any]:
    """An analysis made outside labpipe (kind imported) cannot be redrawn. Check instead that
    each source file is still there with the recorded sha256, and that the copies labpipe made
    from them (results.json, the figures) still match the hashes in the record."""
    def state(path: Path, recorded: str) -> str:
        if not path.is_file():
            return "missing"
        return "unchanged" if sha256_file(path) == recorded else "changed"

    sources = [{"file": s.get("relative_path") or s["path"], "recorded": s["sha256"],
                "state": state(_input_path(s, config), s["sha256"])}
               for s in record.get("sources") or []]
    copies = [{"file": name, "recorded": digest,
               "state": state(provenance_path.parent / name, digest)}
              for name, digest in record["outputs"].items()]
    problems = [f"{item['file']}: {item['state']}" for item in sources + copies
                if item["state"] != "unchanged"]
    return {"made_by": "outside labpipe", "redrawn": False, "checked": True,
            "message": "made outside labpipe: checked, not redrawn",
            "sources": sources, "copies": copies, "problems": problems,
            "all_outputs_identical": not problems}


def replay(provenance_path: Path, config: dict, *, force: bool = False) -> dict[str, Any]:
    record = json.loads(provenance_path.read_text(encoding="utf-8"))
    if str(record.get("schema")).startswith("labpipe.compare-figure/"):      # a comparison figure of samples
        from labpipe import compare
        return compare.replay(provenance_path, config, force=force)
    if record.get("made_by") == "outside labpipe":       # made elsewhere: checked, not redrawn
        return _check_outside(record, provenance_path, config)
    problems, notes = [], environment_notes(record)
    code = git_info()
    recorded = record["code"]
    if recorded.get("analysis_version"):
        from labpipe import versions
        now = versions.describe(record["method"])
        for key in ("analysis_version", "plot_version"):
            if recorded.get(key) != now[key]:
                problems.append(f"{record['method']} {key.replace('_', ' ')} differs: recorded "
                                f"{recorded.get(key)}, current {now[key]}")
        if code["commit"] != recorded["commit"]:
            notes.append(f"made with commit {recorded['commit']}, now {code['commit']}; the "
                         f"{record['method']} analysis and plot versions are the same")
    else:   # records from before per-method versions: the commit, clean
        if code["commit"] != recorded["commit"]:
            problems.append(f"code commit differs: recorded {recorded['commit']}, "
                            f"current {code['commit']}")
        if code["dirty"]:
            problems.append("working tree has uncommitted changes")
        if recorded.get("dirty"):
            problems.append("the original run was made from a dirty working tree")
    input_path = _input_path(record["input"], config)
    if not input_path.exists() and (moved := _moved_input(record["input"], record["method"],
                                                          config)):
        input_path = moved[0]
        notes.append(f"input moved: found at {moved[1]} by its sha256")
    if not input_path.exists():
        return {"error": f"input not found: {input_path}"}
    if sha256_file(input_path) != record["input"]["sha256"]:
        return {"error": "input file content differs from the recorded sha256"}
    for reference in record.get("references", []):
        # shipped references come with the code; the lab's own are in the project (lab/)
        path = (PROJECT_ROOT if reference.get("root") == "project" else CODE_ROOT) / reference["file"]
        if not path.exists() or sha256_file(path) != reference["sha256"]:
            problems.append(f"reference {reference['file']} differs from the recorded hash")
    if problems and not force:
        return {"error": "replay refused", "problems": problems,
                "hint": "use the code that made it (its release or commit), or pass --force to "
                        "compare anyway"}

    stamp = utc_now().replace(":", "").replace("-", "")
    work = PROJECT_ROOT / config["logs_dir"] / "replay" / stamp
    labmethods.register()           # a lab method's code is labpipe_lab.<name>… in the project
    module = importlib.import_module(record["code"]["labpipe_entry"].rsplit(".", 1)[0])
    # The resolved settings are taken from provenance itself: the settings files
    # may have changed since, the record does not.
    module.run(input_path, work / "out", settings=record["parameters"]["resolved"],
               settings_sources=record["parameters"].get("sources"), context={
        "sample_id": record.get("sample_id"), "measurement_id": record.get("measurement_id"),
        "relative_path": record["input"].get("relative_path"),
        "raw_root": record["input"].get("raw_root"), "method": record.get("method"),
        "entry_id": record.get("entry_id")},
        pipeline=True)
    comparison = {}
    for name in _compared(record):
        recorded = record["outputs"].get(name)
        made = work / "out" / name
        regenerated = sha256_file(made) if made.is_file() else None
        comparison[name] = {"recorded": recorded, "regenerated": regenerated,
                            "identical": recorded == regenerated}
    return {"replay_dir": work.relative_to(PROJECT_ROOT).as_posix(), "problems": problems,
            "notes": notes,
            "comparison": comparison,
            "all_outputs_identical": all(item["identical"] for item in comparison.values())}
