"""The lab's own XRD references live in the project (lab/references/xrd), which updates never
touch; the shipped ones stay with the code."""

from __future__ import annotations

import json
import shutil

from labpipe import versions
from tests.conftest import fcc_positions, synthetic_pattern, xrdml_text
from tests.test_pipeline import REPO, _labpipe, _project


def _lab_reference(project, phase_id="Pd-lab", source="Pd"):
    folder = project / "lab" / "references" / "xrd" / "patterns"
    folder.mkdir(parents=True, exist_ok=True)
    data = json.loads((REPO / "references" / "xrd" / "patterns" / f"{source}.json")
                      .read_text(encoding="utf-8"))
    data["id"] = phase_id
    (folder / f"{phase_id}.json").write_text(json.dumps(data), encoding="utf-8")
    (project / "lab" / "references" / "xrd" / "phases.yaml").write_text(
        f"phases:\n  - id: {phase_id}\n    cif: cif/{phase_id}.cif\n    structure_type: fcc\n"
        f"    role: reference\n    note: test\n", encoding="utf-8")


def _analysed(tmp_path):
    project, raw, env = _project(tmp_path)
    positions = fcc_positions(3.8898)
    x, y = synthetic_pattern([(positions[0], 6000.0, 1.2, 0.6), (positions[1], 3000.0, 1.4, 0.6),
                              (positions[2], 2200.0, 1.8, 0.6)])
    (raw / "XRD" / "SYN-1_test.xrdml").write_text(xrdml_text(x, y, "SYN-1"), encoding="utf-8")
    return project, env


def test_lab_references_are_used_and_recorded_with_their_root(tmp_path):
    project, env = _analysed(tmp_path)
    _lab_reference(project)
    _labpipe(env, "scan")
    [pending] = _labpipe(env, "pending")
    assert _labpipe(env, "xrd", "analyze", "--entry", pending["entry_id"])["status"] == "done"
    entry = json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"][
        pending["entry_id"]]
    record = json.loads((project / entry["output_dir"] / "provenance.json").read_text(
        encoding="utf-8"))
    roots = {r["id"]: (r["root"], r["file"]) for r in record["references"]}
    assert roots["Pd-lab"] == ("project", "lab/references/xrd/patterns/Pd-lab.json")
    assert roots["Pd"] == ("code", "references/xrd/patterns/Pd.json")
    listed = {row["id"]: row["source"] for row in _labpipe(env, "xrd", "refs", "list")}
    assert listed["Pd-lab"] == "lab" and listed["Pd"] == "labpipe"


def test_a_lab_reference_may_not_reuse_a_shipped_id(tmp_path):
    project, env = _analysed(tmp_path)
    _lab_reference(project, phase_id="Pd")
    _labpipe(env, "scan")
    [pending] = _labpipe(env, "pending")
    result = _labpipe(env, "xrd", "analyze", "--entry", pending["entry_id"])
    assert result["status"] == "failed" and "come with labpipe" in json.dumps(result)


def test_lab_references_are_part_of_the_xrd_analysis_version(tmp_path, monkeypatch):
    from labpipe import safety
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(safety, "PROJECT_ROOT", project)
    before, eds_before = versions.describe("xrd"), versions.describe("eds")
    _lab_reference(project)
    after = versions.describe("xrd")
    assert after["analysis_version"] != before["analysis_version"]
    assert "lab/references/xrd/patterns/Pd-lab.json" in after["analysis_data"]
    assert versions.describe("eds")["analysis_version"] == eds_before["analysis_version"]
