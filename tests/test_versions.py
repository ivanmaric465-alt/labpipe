"""Analysis version and plot version per method (src/labpipe/versions.py): what changes them,
what does not, and how the scanner uses them. Real source files are never edited: tests change
a copy of the package and point the hashing (or a subprocess) at it."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from labpipe import versions
from tests.conftest import fcc_positions, synthetic_pattern, xrdml_text
from tests.test_pipeline import PATTERN, REPO, _labpipe, _project  # noqa: F401

PEAKS = [(p, a, w, 0.6) for p, a, w in zip(fcc_positions(3.8898), (6000.0, 3000.0, 2200.0, 2000.0),
                                           (1.2, 1.4, 1.8, 2.1))]


def _snapshot() -> dict[str, tuple[str, str]]:
    return {name: (info["analysis_version"], info["plot_version"])
            for name, info in versions.all_versions().items()}


def _append(path: Path, text: str = "\n# changed\n") -> None:
    path.write_bytes(path.read_bytes() + text.encode())


@pytest.fixture
def src_copy(tmp_path, monkeypatch):
    """A copy of the package that the hashing reads instead of the real one."""
    root = tmp_path / "srccopy"
    shutil.copytree(REPO / "src" / "labpipe", root / "labpipe",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(versions, "SOURCE_ROOT", root)
    return root / "labpipe"


def test_versions_are_short_hashes_and_deterministic():
    first = _snapshot()
    assert first == _snapshot()
    assert all(len(a) == len(p) == 16 for a, p in first.values())
    assert len({a for a, _ in first.values()}) == len(first)


def test_what_is_in_each_analysis_version():
    xrd = versions.describe("xrd")
    assert "labpipe.methods.xrd.fitting" in xrd["analysis_modules"]
    assert "labpipe.naming" in xrd["analysis_modules"]          # a sample ID check: it can matter
    assert "labpipe.methods.xrd.plot" not in xrd["analysis_modules"]
    assert xrd["plot_modules"] == ["labpipe.methods.xrd.plot", "labpipe.plotting",
                                   "labpipe.plotting.acs"]
    assert xrd["analysis_data"] and all(f.startswith("references/xrd/patterns/")
                                        for f in xrd["analysis_data"])
    for name in ("eds", "sem"):                                  # they draw inside analyze.py
        info = versions.describe(name)
        assert info["analysis_modules"] == [f"labpipe.methods.{name}",
                                            f"labpipe.methods.{name}.analyze"]
    everything = {m for info in versions.all_versions().values() for m in info["analysis_modules"]}
    assert not everything & versions.INFRASTRUCTURE
    assert not any(m.startswith("labpipe.plotting") for m in everything)


def test_docs_report_agents_and_infrastructure_change_no_version(src_copy):
    before = _snapshot()
    for name in ("report.py", "agents.py", "postdoc.py", "safety.py", "provenance.py",
                 "settings.py", "scanner.py", "cli.py"):
        _append(src_copy / name)
    (src_copy.parent / "NOTES.md").write_text("docs are not even read", encoding="utf-8")
    assert _snapshot() == before


def test_a_change_in_xrd_fitting_changes_only_xrd_analysis_version(src_copy):
    before = _snapshot()
    _append(src_copy / "methods" / "xrd" / "fitting.py")
    after = _snapshot()
    assert after["xrd"][0] != before["xrd"][0] and after["xrd"][1] == before["xrd"][1]
    assert after["eds"] == before["eds"] and after["sem"] == before["sem"]


def test_a_change_in_xrd_plot_changes_only_xrd_plot_version(src_copy):
    before = _snapshot()
    _append(src_copy / "methods" / "xrd" / "plot.py")
    after = _snapshot()
    assert after["xrd"][1] != before["xrd"][1] and after["xrd"][0] == before["xrd"][0]
    assert after["eds"] == before["eds"] and after["sem"] == before["sem"]


def test_shared_plot_style_changes_every_plot_version_and_no_analysis_version(src_copy):
    before = _snapshot()
    _append(src_copy / "plotting" / "acs.py")
    after = _snapshot()
    for name in before:
        assert after[name][0] == before[name][0] and after[name][1] != before[name][1]


def test_drawing_inside_analyze_is_part_of_the_analysis_version(src_copy):
    before = _snapshot()
    _append(src_copy / "methods" / "eds" / "analyze.py")
    after = _snapshot()
    assert after["eds"][0] != before["eds"][0] and after["eds"][1] == before["eds"][1]
    assert after["xrd"] == before["xrd"]


def test_reference_data_is_part_of_the_xrd_analysis_version(tmp_path, monkeypatch):
    from labpipe import safety
    shutil.copytree(REPO / "references", tmp_path / "references")
    monkeypatch.setattr(safety, "CODE_ROOT", tmp_path)
    before = _snapshot()
    _append(tmp_path / "references" / "xrd" / "patterns" / "Pt.json", " ")
    after = _snapshot()
    assert after["xrd"][0] != before["xrd"][0] and after["eds"] == before["eds"]


def test_hashing_ignores_line_endings(src_copy):
    before = _snapshot()
    for path in src_copy.rglob("*.py"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert _snapshot() == before


def test_a_lazy_import_inside_a_function_is_followed(src_copy):
    module = src_copy / "methods" / "sem" / "analyze.py"
    module.write_text(module.read_text(encoding="utf-8") + "\n\ndef _late():\n"
                      "    from labpipe.methods.xrd import lattice\n    return lattice\n",
                      encoding="utf-8")
    assert "labpipe.methods.xrd.lattice" in versions.describe("sem")["analysis_modules"]


def test_methods_versions_command_prints_json(tmp_path):
    project, _, env = _project(tmp_path)
    out = _labpipe(env, "methods", "versions")
    assert set(out) == {"xrd", "eds", "sem"}
    assert out["xrd"]["analysis_version"] == versions.describe("xrd")["analysis_version"]
    assert out["xrd"]["plot_modules"] and out["xrd"]["analysis_modules"]


# --- the scanner and the manifest -----------------------------------------------------------

@pytest.fixture(scope="module")
def analysed(tmp_path_factory):
    """A project with one analysed XRD entry (real analysis, synthetic data)."""
    tmp = tmp_path_factory.mktemp("analysed")
    project, raw, env = _project(tmp)
    x, y = synthetic_pattern(PEAKS)
    (raw / "XRD" / "SYN-1_test.xrdml").write_text(xrdml_text(x, y, "SYN-1"), encoding="utf-8")
    _labpipe(env, "scan")
    [pending] = _labpipe(env, "pending")
    result = _labpipe(env, "xrd", "analyze", "--entry", pending["entry_id"])
    assert result["status"] == "done", result
    return project, env, pending["entry_id"]


@pytest.fixture
def project(analysed, tmp_path):
    """A private copy of the analysed project, and a runner using a copy of the package."""
    source, env, eid = analysed
    target = tmp_path / "project"
    shutil.copytree(source, target)
    code = tmp_path / "src"
    shutil.copytree(REPO / "src", code, ignore=shutil.ignore_patterns("__pycache__"))

    run_env = dict(env, LABPIPE_ROOT=str(target), LABPIPE_PROJECT=str(target),
                   LABPIPE_CODE=str(REPO), PYTHONPATH=str(code))

    class Project:
        root, entry_id, src = target, eid, code / "labpipe"
        env = run_env

        def run(self, *args):
            return _labpipe(self.env, *args)

        def entry(self):
            return json.loads((target / "manifest.json").read_text(encoding="utf-8"))["entries"][eid]

        def edit_entry(self, **changes):
            path = target / "manifest.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            for key, value in changes.items():
                if value is None:
                    data["entries"][eid].pop(key, None)
                else:
                    data["entries"][eid][key] = value
            path.write_text(json.dumps(data), encoding="utf-8")
    return Project()


def test_manifest_and_provenance_record_both_versions(project):
    entry = project.entry()
    expected = project.run("methods", "versions")["xrd"]
    assert entry["analysis_version"] == expected["analysis_version"]
    assert entry["plot_version"] == expected["plot_version"]
    record = json.loads((project.root / entry["output_dir"] / "provenance.json").read_text(encoding="utf-8"))
    assert record["code"]["analysis_version"] == expected["analysis_version"]
    assert record["code"]["plot_version"] == expected["plot_version"]
    assert "commit" in record["code"]


def test_replay_still_reproduces_with_versions_recorded(project):
    entry = project.entry()
    # --force: the commit check is replay's own (a clean tree); here only the outputs matter
    out = project.run("replay", str(project.root / entry["output_dir"] / "provenance.json"),
                      "--force")
    assert out["all_outputs_identical"] is True, out


def test_unrelated_code_changes_leave_the_entry_done(project):
    for path in (project.src / "report.py", project.src / "methods" / "eds" / "analyze.py",
                 project.src / "safety.py"):
        _append(path)
    scan = project.run("scan")
    assert scan["changes"] == [] and project.entry()["status"] == "done"


def test_a_changed_calculation_marks_stale_with_the_reason(project):
    old = project.entry()["analysis_version"]
    _append(project.src / "methods" / "xrd" / "fitting.py")
    scan = project.run("scan")
    entry = project.entry()
    new = project.run("methods", "versions")["xrd"]["analysis_version"]
    assert entry["status"] == "stale" and new != old
    assert entry["status_reason"] == f"the xrd calculation changed (version {old} → {new})"
    assert scan["changes"][0]["to"] == "stale"


def test_a_changed_plot_module_marks_the_plot_out_of_date_not_stale(project):
    _append(project.src / "methods" / "xrd" / "plot.py")
    project.run("scan")
    entry = project.entry()
    assert entry["status"] == "done" and entry["plot_outdated"] is True
    assert "plot code" in entry["plot_outdated_reason"]
    replotted = project.run("replot")
    assert replotted["replotted"] == [project.entry_id], replotted
    entry = project.entry()
    assert entry["plot_outdated"] is False
    assert entry["plot_version"] == project.run("methods", "versions")["xrd"]["plot_version"]
    project.run("scan")
    assert project.entry()["plot_outdated"] is False


def test_failed_entries_retry_when_the_calculation_changes_not_the_commit(project):
    project.edit_entry(status="failed", status_reason="boom", attempts=1,
                       code_version="some-other-commit")
    project.run("scan")
    assert project.entry()["status"] == "failed"        # other commit, same calculation
    _append(project.src / "methods" / "xrd" / "peaks.py")
    project.run("scan")
    entry = project.entry()
    assert entry["status"] == "pending" and "code changed" in entry["status_reason"]


def test_failed_entry_without_a_version_retries_on_a_new_commit(project):
    project.edit_entry(status="failed", status_reason="boom", attempts=1,
                       analysis_version=None, plot_version=None, code_version="old-commit")
    project.run("scan")
    assert project.entry()["status"] == "pending"


def test_migration_adopts_the_versions_when_analysed_with_the_current_commit(project):
    expected = project.run("methods", "versions")["xrd"]
    current = project.run("scan")["code_version"]
    if current.endswith("+dirty"):
        pytest.skip("adopting needs a clean working tree (the rule under test)")
    project.edit_entry(analysis_version=None, plot_version=None, code_version=current)
    project.run("scan")
    entry = project.entry()
    assert entry["status"] == "done"
    assert entry["analysis_version"] == expected["analysis_version"]
    assert entry["plot_version"] == expected["plot_version"]


def test_migration_marks_stale_when_analysed_with_another_commit(project):
    project.edit_entry(analysis_version=None, plot_version=None, code_version="0123456789ab")
    project.run("scan")
    entry = project.entry()
    assert entry["status"] == "stale"
    assert entry["status_reason"] == ("analysed before per-method versions; redo to be sure "
                                      "(`labpipe requeue --stale`)")


def test_migration_does_not_adopt_a_dirty_commit(project):
    project.edit_entry(analysis_version=None, plot_version=None,
                       code_version="uncommitted+dirty")
    project.run("scan")
    assert project.entry()["status"] == "stale"


def test_a_stale_entry_is_redone_on_requeue_and_records_the_new_version(project):
    _append(project.src / "methods" / "xrd" / "fitting.py")
    project.run("scan")
    project.run("requeue", "--stale")
    out = project.run("xrd", "analyze", "--entry", project.entry_id)
    assert out["status"] == "done", out
    entry = project.entry()
    assert entry["analysis_version"] == project.run("methods", "versions")["xrd"]["analysis_version"]
    assert project.run("scan")["changes"] == []
