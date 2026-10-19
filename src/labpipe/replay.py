"""Regenerate an analysis from its provenance.json alone and compare output hashes.

Uses: the input path + sha256, the resolved settings (stored in full), the recorded code
commit (must equal the current clean HEAD unless --force) and the reference
files (checked by sha256). Output goes to logs/replay/<stamp>/.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from labpipe.provenance import git_info, sha256_file, utc_now
from labpipe.safety import CODE_ROOT, PROJECT_ROOT


def _compared(record: dict) -> list[str]:
    """results.json and every figure the record lists (plot.*, zoom.*)."""
    return ["results.json"] + [name for name in record["outputs"]
                               if name.startswith(("plot.", "zoom."))]


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


def replay(provenance_path: Path, config: dict, *, force: bool = False) -> dict[str, Any]:
    record = json.loads(provenance_path.read_text(encoding="utf-8"))
    problems = []
    code = git_info()
    if code["commit"] != record["code"]["commit"]:
        problems.append(f"code commit differs: recorded {record['code']['commit']}, "
                        f"current {code['commit']}")
    if code["dirty"]:
        problems.append("working tree has uncommitted changes")
    if record["code"].get("dirty"):
        problems.append("the original run was made from a dirty working tree")
    input_path = _input_path(record["input"], config)
    if not input_path.exists():
        return {"error": f"input not found: {input_path}"}
    if sha256_file(input_path) != record["input"]["sha256"]:
        return {"error": "input file content differs from the recorded sha256"}
    for reference in record.get("references", []):
        path = CODE_ROOT / reference["file"]   # references come with the code
        if not path.exists() or sha256_file(path) != reference["sha256"]:
            problems.append(f"reference {reference['file']} differs from the recorded hash")
    if problems and not force:
        return {"error": "replay refused", "problems": problems,
                "hint": "check out the recorded commit, or pass --force to compare anyway"}

    stamp = utc_now().replace(":", "").replace("-", "")
    work = PROJECT_ROOT / config["logs_dir"] / "replay" / stamp
    module = importlib.import_module(record["code"]["labpipe_entry"].rsplit(".", 1)[0])
    # The resolved settings are taken from provenance itself: the settings files
    # may have changed since, the record does not.
    module.run(input_path, work / "out", settings=record["parameters"]["resolved"],
               settings_sources=record["parameters"].get("sources"), context={
        "sample_id": record.get("sample_id"), "measurement_id": record.get("measurement_id"),
        "relative_path": record["input"].get("relative_path"),
        "raw_root": record["input"].get("raw_root")}, pipeline=True)
    comparison = {}
    for name in _compared(record):
        recorded = record["outputs"].get(name)
        regenerated = sha256_file(work / "out" / name)
        comparison[name] = {"recorded": recorded, "regenerated": regenerated,
                            "identical": recorded == regenerated}
    return {"replay_dir": str(work.relative_to(PROJECT_ROOT)), "problems": problems,
            "comparison": comparison,
            "all_outputs_identical": all(item["identical"] for item in comparison.values())}
