"""Naming, path safety, parser regression and an end-to-end scan/analyse run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from labpipe.naming import derive_sample_id, measurement_id
from tests.conftest import fcc_positions, synthetic_pattern, xrdml_text

REPO = Path(__file__).resolve().parents[1]
PATTERN = r"^[A-Za-z][A-Za-z0-9]*-[0-9]+[A-Za-z]?$"
# Optional: a checkout of the code the XRD readers were ported from (docs/PORTING.md).
UPSTREAM = Path(os.environ.get("LABPIPE_UPSTREAM_ELN", Path(__file__).parent / "no-upstream"))


@pytest.mark.parametrize("path, expected", [
    ("XRD/S-9_1.2.26.xrdml", "S-9"),
    ("XRD/S-11.xrdml", "S-11"),
    ("XRD/S-17_3.2.26_long scan + Si.xrdml", "S-17"),
    ("XRD/AB-3_12.3.2025. extra, notes.xrdml", "AB-3"),
    ("XRD/S-6-7-8 comparison.xrdml", None),
    ("XRD/scan.xrdml", None),
])
def test_sample_id_rule(path, expected):
    sample, _, problem = derive_sample_id(path, pattern=PATTERN, overrides={})
    assert sample == expected
    assert (problem is None) == (expected is not None)


def test_sample_id_override_wins():
    sample, source, problem = derive_sample_id("XRD/scan.xrdml", pattern=PATTERN,
                                               overrides={"XRD/scan.xrdml": "S-99"})
    assert (sample, problem) == ("S-99", None)
    assert "override" in source


def test_measurement_id_is_filesystem_safe():
    assert measurement_id("XRD/S-17_3.2.26_long scan + Si.xrdml") == \
        "S-17_3.2.26_long_scan_Si"


def test_safety_refuses_protected_and_outside_paths(tmp_path):
    from labpipe import safety
    config = {"raw_roots": [{"id": "r", "path": str(tmp_path / "raw")}],
              "protected_paths": [str(tmp_path / "sync")], "analysis_dir": "analysis",
              "logs_dir": "logs", "manifest": "manifest.json"}
    (tmp_path / "raw").mkdir()
    with pytest.raises(safety.UnsafePathError):
        safety.assert_not_protected(tmp_path / "raw" / "x.json", config)
    with pytest.raises(safety.UnsafePathError):
        safety.assert_not_protected(tmp_path / "sync" / "deep" / "x.json", config)
    with pytest.raises(safety.UnsafePathError):
        safety.assert_pipeline_writable(safety.PROJECT_ROOT / "src" / "x.py", config)
    safety.assert_pipeline_writable(safety.PROJECT_ROOT / "analysis" / "S-1" / "x.json", config)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra privileges on Windows")
def test_symlink_escape_is_refused(tmp_path):
    from labpipe import safety
    raw = tmp_path / "raw"
    raw.mkdir()
    link = tmp_path / "innocent"
    link.symlink_to(raw)
    config = {"raw_roots": [{"id": "r", "path": str(raw)}], "protected_paths": [],
              "analysis_dir": "analysis", "logs_dir": "logs", "manifest": "manifest.json"}
    with pytest.raises(safety.UnsafePathError):
        safety.assert_not_protected(link / "x.json", config)


@pytest.mark.skipif(not (UPSTREAM / "analysis_files").exists(),
                    reason="set LABPIPE_UPSTREAM_ELN to the upstream checkout")
def test_ported_parsers_match_upstream():
    """Same points as upstream-eln@a3931a4 on its real example files (no decimation there)."""
    from labpipe.methods.xrd.io_xrdml import read_xrdml
    from labpipe.methods.xrd.io_xy import read_xy
    code = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        "from utils.scientific_reports import charts as c;"
        "f = c._charts_from_xrdml if sys.argv[2].endswith('.xrdml') else c._charts_from_xy;"
        "print(json.dumps(f(sys.argv[2])[0]['points']))")
    files = sorted((UPSTREAM / "analysis_files").glob("*.xrdml")) + sorted(
        (UPSTREAM / "analysis_files").glob("*.xy"))
    assert files
    for path in files:
        upstream = json.loads(subprocess.run(
            [sys.executable, "-I", "-c", code, str(UPSTREAM), str(path)],
            capture_output=True, text=True, check=True).stdout)
        ours = (read_xrdml if path.suffix == ".xrdml" else read_xy)(path).scans[0]
        assert len(upstream) == ours.two_theta.size
        assert np.allclose([p[0] for p in upstream], ours.two_theta, atol=1e-9, rtol=0)
        assert np.array_equal([p[1] for p in upstream], ours.intensity)


def _project(tmp_path: Path) -> tuple[Path, Path, dict]:
    project = tmp_path / "project"
    for item in ("config", "references", "methods.yaml"):
        source = REPO / item
        (shutil.copytree if source.is_dir() else shutil.copy)(source, project / item)
    raw = tmp_path / "raw"
    (raw / "XRD").mkdir(parents=True)
    (project / "config" / "local.yaml").write_text(
        f"raw_roots: [{{id: lab, path: '{raw.as_posix()}'}}]\nprotected_paths: []\n")
    for steering in (project / "config" / "measurements").glob("**/*.yaml"):
        steering.unlink()  # the user's own steering files are not part of the test
    env = dict(os.environ, LABPIPE_ROOT=str(project))
    # settle time 0 for the test
    config_file = project / "config" / "pipeline.yaml"
    config_file.write_text(config_file.read_text().replace("settle_minutes: 10",
                                                           "settle_minutes: 0"))
    return project, raw, env


def _labpipe(env, *args) -> dict:
    completed = subprocess.run([sys.executable, "-m", "labpipe.cli", *args], env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert completed.returncode in (0, 1), completed.stderr
    return json.loads(completed.stdout)


def test_end_to_end_scan_analyse_rescan(tmp_path):
    project, raw, env = _project(tmp_path)
    positions = fcc_positions(3.8898)
    peaks = [(positions[0], 6000.0, 1.2, 0.6), (positions[1], 3000.0, 1.4, 0.6),
             (positions[2], 2200.0, 1.8, 0.6), (positions[3], 2000.0, 2.1, 0.6)]
    x, y = synthetic_pattern(peaks)
    (raw / "XRD" / "SYN-1_test.xrdml").write_text(xrdml_text(x, y, "SYN-1"))
    (raw / "XRD" / "SYN-1_test.xy").write_text("\n".join(f"{a} {b}" for a, b in zip(x, y)))
    (raw / "XRD" / "SYN-1_test.rd").write_bytes(b"\x00binary")
    (raw / "XRD" / "weird name.xrdml").write_text(xrdml_text(x, y))
    raw_before = {p.name: p.read_bytes() for p in (raw / "XRD").iterdir()}

    first = _labpipe(env, "scan")
    assert first["counts"] == {"duplicate": 1, "ignored": 1, "needs_review": 1, "pending": 1}
    second = _labpipe(env, "scan")
    assert second["changes"] == []  # idempotent

    pending = _labpipe(env, "pending")
    assert [row["relative_path"] for row in pending] == ["XRD/SYN-1_test.xrdml"]
    result = _labpipe(env, "xrd", "analyze", "--entry", pending[0]["entry_id"])
    assert result["status"] == "done", result
    out = project / result["output_dir"]
    assert sorted(p.name for p in out.iterdir()) == ["plot.pdf", "plot.png",
                                                    "provenance.json", "results.json"]
    results = json.loads((out / "results.json").read_text())
    phase = results["phases"]["fcc_indexed"][0]
    assert phase["a_angstrom"] == pytest.approx(3.8898, abs=0.002)
    assert (project / "analysis" / "SYN-1" / "xrd" / "index.json").exists()

    # raw data untouched
    assert {p.name: p.read_bytes() for p in (raw / "XRD").iterdir()} == raw_before

    # a changed file is reprocessed; old outputs are superseded, not deleted
    time.sleep(0.01)
    x2, y2 = synthetic_pattern(peaks, seed=11)
    (raw / "XRD" / "SYN-1_test.xrdml").write_text(xrdml_text(x2, y2, "SYN-1"))
    third = _labpipe(env, "scan")
    assert [c["to"] for c in third["changes"]] == ["pending"]
    _labpipe(env, "xrd", "analyze", "--entry", pending[0]["entry_id"])
    superseded = list((project / "analysis" / "SYN-1" / "xrd" / "_superseded").rglob(
        "results.json"))
    assert len(superseded) == 1


def test_settings_precedence_and_typos(tmp_path, monkeypatch):
    from labpipe import settings
    config = tmp_path / "config"
    (config / "measurements" / "xrd").mkdir(parents=True)
    (config / "xrd.yaml").write_text("background: {order: 4, anchor_ranges_deg: []}\n"
                                     "phases: null\n")
    (config / "samples.yaml").write_text(
        "presets: {p1: {xrd: {background: {order: 3}, phases: [Pt]}}}\n"
        "samples: {S-1: {presets: [p1], xrd: {phases: [Pd]}}}\n")
    (config / "measurements" / "xrd" / "S-1_a.yaml").write_text(
        "note: user asked on 2026-10-16\nbackground: {anchor_ranges_deg: [[30, 35]]}\n")
    monkeypatch.setattr(settings, "CONFIG", config)
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    resolved, sources = settings.resolve("xrd", "S-1", "S-1_a")
    assert resolved == {"background": {"order": 3, "anchor_ranges_deg": [[30, 35]]},
                        "phases": ["Pd"]}
    assert [s["layer"].split()[0] for s in sources] == ["global", "sample", "measurement"]
    (config / "measurements" / "xrd" / "S-1_a.yaml").write_text("backgruond: {order: 2}\n")
    with pytest.raises(settings.SettingsError, match="backgruond"):
        settings.resolve("xrd", "S-1", "S-1_a")


def test_template_method_fulfils_the_contract(tmp_path):
    """The copy-and-adapt template must keep working (docs/ADDING_A_METHOD.md)."""
    import yaml
    from labpipe.methods.template import analyze as template
    data = tmp_path / "S-1_curve.txt"
    data.write_text("# x y\n" + "\n".join(f"{i} {i * (20 - i)}" for i in range(21)))
    settings = yaml.safe_load((Path(template.__file__).parent / "config.example.yaml").read_text())
    summary = template.run(data, tmp_path / "out", settings=settings, pipeline=False,
                           context={"sample_id": "S-1", "measurement_id": "S-1_curve"})
    assert summary["status"] == "ok"
    assert set(summary["outputs"]) == {"results.json", "plot.png", "plot.pdf"}
    results = json.loads((tmp_path / "out" / "results.json").read_text())
    assert results["summary"]["x_at_y_max"] == 10.0
    record = json.loads((tmp_path / "out" / "provenance.json").read_text())
    assert record["input"]["sha256"] == results["input"]["sha256"]
    assert record["parameters"]["resolved"] == settings


def test_refs_add_registers_and_builds_a_user_cif(tmp_path, monkeypatch):
    pytest.importorskip("pymatgen")
    from labpipe.methods.xrd import refbuild
    references = tmp_path / "references" / "xrd"
    shutil.copytree(REPO / "references" / "xrd", references)
    monkeypatch.setattr(refbuild, "REFERENCES", references)
    monkeypatch.setattr(refbuild, "CATALOGUE", references / "phases.yaml")
    user_cif = tmp_path / "my_Pt.cif"
    shutil.copy(references / "cif" / "9008480.cif", user_cif)
    data = refbuild.add(user_cif, phase_id="Pt-mine", structure_type="fcc", role="reference",
                        cod_id=None, note="test copy")
    assert data["id"] == "Pt-mine" and data["source"]["database"] == "user-supplied CIF"
    assert (references / "cif" / "Pt-mine.cif").exists()
    assert any(row["id"] == "Pt-mine" and row["built"] for row in refbuild.listing())
    with pytest.raises(SystemExit):
        refbuild.add(user_cif, phase_id="Pt-mine", structure_type="fcc", role="reference",
                     cod_id=None, note=None)


def test_summary_and_report(tmp_path):
    project, raw, env = _project(tmp_path)
    positions = fcc_positions(3.8898)
    peaks = [(positions[0], 6000.0, 1.2, 0.6), (positions[1], 3000.0, 1.4, 0.6),
             (positions[2], 2200.0, 1.8, 0.6), (positions[3], 2000.0, 2.1, 0.6)]
    x, y = synthetic_pattern(peaks)
    (raw / "XRD" / "SYN-2_test.xrdml").write_text(xrdml_text(x, y, "SYN-2"))
    _labpipe(env, "scan")
    entry = _labpipe(env, "pending")[0]["entry_id"]
    _labpipe(env, "xrd", "analyze", "--entry", entry)
    summary = _labpipe(env, "xrd", "summary", "--entry", entry)
    assert float(summary["phases"][0]["lattice_a"].split()[0]) == pytest.approx(3.8898, abs=0.002)
    assert summary["phases"][0]["reflections"] == "(111) (200) (220) (311)"
    assert all(set(item) == {"code", "text"} for item in summary["check_before_using"])
    report = _labpipe(env, "report")
    page = (project / report["report"]).read_text(encoding="utf-8")
    assert report["measurements"] == 1 and "SYN-2" in page and "No notes yet" in page


def test_report_shows_eds_panels_and_sem_gallery_one_at_a_time(tmp_path):
    from tests import test_eds, test_sem
    project, raw, env = _project(tmp_path)
    test_sem.make_example(raw / "SEM", "S-14 a001", mag=10000)
    second = test_sem.make_example(raw / "SEM", "S-14 a002", mag=50000)
    second.write_bytes(second.read_bytes()[:-1] + b"\x01")   # not a byte-identical duplicate
    test_eds.make_example(raw / "EDS", "S-13")
    (raw / "EDS" / "S-14.htm").write_text(test_eds.REPORT.format(stem="S-13") + "\n",
                                         encoding="latin-1")
    for n in (0, 1):
        (raw / "EDS" / f"s-14{n}.jpg").write_bytes((raw / "EDS" / f"s-13{n}.jpg").read_bytes())
    steering = project / "config" / "measurements" / "eds" / "S-14.yaml"
    steering.parent.mkdir(parents=True, exist_ok=True)
    steering.write_text("note: the report links S-13's images\nimages: [s-140.jpg, s-141.jpg]\n")
    counts = _labpipe(env, "scan")["counts"]
    assert counts.get("pending") == 4 and "unclaimed" not in counts      # s-14x.jpg claimed
    _labpipe(env, "analyze", "--pending", "--method", "sem")
    for item in _labpipe(env, "pending"):
        _labpipe(env, "eds", "analyze", "--entry", item["entry_id"])
    results = json.loads((project / "analysis" / "S-14" / "eds" / "S-14" / "results.json")
                         .read_text(encoding="utf-8"))
    assert [i["file"] for i in results["input"]["images"]] == ["s-140.jpg", "s-141.jpg"]
    page = (project / _labpipe(env, "report")["report"]).read_text(encoding="utf-8")
    assert page.count("data-car tabindex") == 3 and page.count("1 / 3") == 2 and "1 / 2" in page
    assert "spectrum (s-141.jpg)" in page and "the report links S-13" in page


def test_roots_rename_keeps_entries(tmp_path):
    project, raw, env = _project(tmp_path)
    x, y = synthetic_pattern([(40.0, 5000.0, 1.0, 0.5)])
    (raw / "XRD" / "SYN-3_test.xrdml").write_text(xrdml_text(x, y, "SYN-3"))
    _labpipe(env, "scan")
    before = _labpipe(env, "pending")[0]
    local = project / "config" / "local.yaml"
    local.write_text(local.read_text().replace("id: lab", "id: renamed"))
    assert _labpipe(env, "roots", "rename", "lab", "renamed")["renamed"] == 1
    assert _labpipe(env, "scan")["changes"] == []          # same files, nothing new
    after = _labpipe(env, "pending")[0]
    assert after["relative_path"] == before["relative_path"] and after["entry_id"] != before["entry_id"]


def test_automation_run_and_setup_files(tmp_path, monkeypatch):
    project, raw, env = _project(tmp_path)
    env["LABPIPE_AGENT_MODE"] = "unattended"
    with open(project / "config" / "local.yaml", "a") as stream:
        stream.write("automation: {notify: {desktop: false}}\n")   # no pop-ups from tests
    assert _labpipe(env, "automation", "run")["status"] == "nothing_to_do"
    x, y = synthetic_pattern([(40.0, 5000.0, 1.0, 0.5)])
    (raw / "XRD" / "SYN-4_test.xrdml").write_text(xrdml_text(x, y, "SYN-4"))
    # no agents configured -> the AI step cannot start; reported, not crashed
    result = _labpipe(env, "automation", "run")
    assert result["status"] == "run_failed" and "harness" in result["reason"]
    log = (project / "logs" / "automation.log").read_text()
    assert '"event": "notify"' in log and "analysis started" in log

    monkeypatch.setattr("labpipe.automation.PROJECT_ROOT", project)
    monkeypatch.setattr("labpipe.safety.PROJECT_ROOT", project)
    from labpipe import automation
    from labpipe.safety import set_local_block
    local = project / "config" / "local.yaml"
    set_local_block("automation", {"settle_minutes": 3, "nightly_at": "02:00"})
    set_local_block("automation", {"settle_minutes": 5, "nightly_at": "03:30"})
    text = local.read_text()
    assert text.count("automation:") == 1 and "settle_minutes: 5" in text and "raw_roots" in text
    units = automation._systemd_units({"claude": "/opt/claude/bin/claude", "nightly_at": "03:30"})
    assert "OnCalendar=*-*-* 03:30:00" in units["labpipe-nightly.timer"]
    assert "automation watch" in units["labpipe-watch.service"]


def test_try_exclusive_does_not_wait(tmp_path):
    from labpipe.locking import try_exclusive
    with try_exclusive(tmp_path / "x.mutex") as first:
        assert first
        if sys.platform != "win32":  # flock is per open file: a second handle is refused
            with try_exclusive(tmp_path / "x.mutex") as second:
                assert second is False


@pytest.mark.parametrize("stored, agree", [("S-15", True), ("S-15_5.2.26", True),
                                           ("S-15 long scan", True), ("S-155", False),
                                           ("S-15A_x", False), ("S-1", False), ("XYZ", False)])
def test_stored_sample_id_may_extend_the_file_name_id(stored, agree):
    from labpipe.naming import sample_ids_agree
    assert sample_ids_agree("S-15", stored) is agree


def test_inventory_baseline_and_batches(tmp_path):
    project, raw, env = _project(tmp_path)
    x, y = synthetic_pattern([(40.0, 5000.0, 1.0, 0.5)])
    for n in (1, 2, 3):
        (raw / "XRD" / f"SYN-{n}_test.xrdml").write_text(xrdml_text(x, y, f"SYN-{n}"))
    (raw / "report.docx").write_bytes(b"not data")
    root = _labpipe(env, "inventory")["roots"][0]
    assert root["recognised"]["xrd"]["files"] == {".xrdml (analysed)": 3}
    assert root["not_recognised"] == {".docx": {"files": 1, "examples": ["report.docx"]}}
    assert root["already_in_manifest"] == 0

    # "only new files from now on": existing files are recorded, not queued
    assert _labpipe(env, "scan", "--baseline")["counts"] == {"baseline": 3, "unclaimed": 1}
    assert _labpipe(env, "scan")["changes"] == []
    assert _labpipe(env, "pending") == []
    (raw / "XRD" / "SYN-4_test.xrdml").write_text(xrdml_text(x, y, "SYN-4"))
    assert _labpipe(env, "scan")["counts"] == {"baseline": 3, "pending": 1, "unclaimed": 1}

    assert len(_labpipe(env, "requeue", "--baseline")["requeued"]) == 3
    pending = _labpipe(env, "pending")
    assert len(pending) == 4
    # inside an automatic run the agents only see that run's batch
    batch = dict(env, LABPIPE_BATCH=pending[0]["entry_id"])
    assert [row["entry_id"] for row in _labpipe(batch, "pending")] == [pending[0]["entry_id"]]


def test_automation_health_reports_failed_runs(tmp_path, monkeypatch):
    from labpipe import automation, manifest
    monkeypatch.setattr(automation, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manifest, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(automation, "_services", lambda: {"labpipe-watch.service": "active"})
    config = {"logs_dir": "logs", "manifest": "manifest.json", "automation": {}}
    log = tmp_path / "logs" / "automation.log"
    log.parent.mkdir()
    assert automation.health(config)["status"] == "ok"   # no run yet
    log.write_text(json.dumps({"utc": "2026-10-16T21:08:00Z", "event": "run_finished",
                               "exit_code": 0, "done": ["a"], "failed": [],
                               "cost_usd": 0.395}) + "\n")
    report = automation.health(config)
    assert report["status"] == "ok" and "cost 0.40 USD" in report["message"]
    with open(log, "a") as stream:
        stream.write(json.dumps({"utc": "2026-10-17T02:00:00Z", "event": "scan_failed",
                                 "reason": "raw root not available"}) + "\n")
    report = automation.health(config)
    assert report["status"] == "warn" and "FAILED" in report["message"] and report["next"]
    (tmp_path / "manifest.json").write_text(json.dumps({"schema": "labpipe.manifest/1", "entries": {
        "x": {"entry_id": "x", "status": "pending",
              "events": [{"utc": "2026-10-10T00:00:00Z", "to": "pending"}]}}}))
    report = automation.health(config, now=time.time())
    assert report["details"]["waiting_long"] == ["x"]


def _starts_with_cm(path):
    with open(path, encoding="utf-8", errors="replace") as stream:
        return stream.read(4) == "$CM_"


def test_recognise_hook_disambiguates_shared_extensions(tmp_path):
    from labpipe.registry import classify
    methods = {"sem": {"patterns": ["**/*.tif"], "ignore": ["**/*.txt"],
                       "recognise": "tests.test_pipeline:_starts_with_cm"},
               "echem": {"patterns": ["**/*.txt"]}}
    (tmp_path / "a001.txt").write_text("$CM_FORMAT JEOL/SEM\n")
    (tmp_path / "LSV.txt").write_text("Potential applied (V);Time (s)\n")
    assert classify("a001.txt", methods, root=tmp_path) == ("sem", "ignore")
    assert classify("LSV.txt", methods, root=tmp_path) == ("echem", "analyse")
    assert classify("LSV.txt", methods) == ("sem", "ignore")   # globs only, without a root


def test_methods_check_passes_for_xrd(tmp_path):
    from labpipe.methods_kit import check
    positions = fcc_positions(3.89)
    x, y = synthetic_pattern([(positions[0], 6000.0, 1.2, 0.6), (positions[1], 3000.0, 1.4, 0.6),
                              (positions[2], 2200.0, 1.8, 0.6), (positions[3], 2000.0, 2.1, 0.6)])
    example = tmp_path / "raw" / "S-1_test.xrdml"
    example.parent.mkdir()
    example.write_text(xrdml_text(x, y, "S-1"))
    result = check("xrd", example, work_dir=tmp_path / "work")
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]
    broken = check("xrd", tmp_path / "raw" / "absent.xrdml", work_dir=tmp_path / "work2")
    assert not broken["ok"]


def test_methods_new_scaffolds_and_never_overwrites(tmp_path):
    project = tmp_path / "project"
    for item in ("methods.yaml", "config", "agents", "src/labpipe/methods/template"):
        source, target = REPO / item, project / item
        target.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if source.is_dir() else shutil.copy)(source, target)
    (project / "tests").mkdir()
    env = dict(os.environ, LABPIPE_ROOT=str(project))
    example = tmp_path / "scan.dat"
    example.write_text("1 2\n")
    created = _labpipe(env, "methods", "new", "demo", "--example", str(example),
                       "--description", "Demo technique")["created"]
    for path in ("src/labpipe/methods/demo/analyze.py", "config/demo.yaml",
                 "agents/demo-analyst.md", "tests/test_demo.py", "docs/methods/demo.md"):
        assert path in created and (project / path).exists()
    analyze = (project / "src/labpipe/methods/demo/analyze.py").read_text()
    assert 'METHOD = "demo"' in analyze and "labpipe.demo.results/1" in analyze
    role = (project / "agents/demo-analyst.md").read_text()
    assert "name: demo-analyst" in role and "labpipe notes verify" in role and "{{" not in role
    import yaml
    entry = yaml.safe_load((project / "methods.yaml").read_text())["methods"]["demo"]
    assert entry["patterns"] == ["**/*.dat"] and entry["subagent"] == "demo-analyst"
    assert "already" in _labpipe(env, "methods", "new", "demo", "--example", str(example),
                                 "--description", "again")["error"]
    assert "name" in _labpipe(env, "methods", "new", "Bad-Name", "--example", str(example),
                              "--description", "x")["error"]
