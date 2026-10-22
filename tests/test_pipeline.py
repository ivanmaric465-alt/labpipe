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


def _project(tmp_path: Path, automation: dict | None = None) -> tuple[Path, Path, dict]:
    """A project folder (only the lab's files); the code and its defaults are the repository's."""
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "labpipe.yaml").write_text("schema: labpipe.project/1\nname: test\n", encoding="utf-8")
    raw = tmp_path / "raw"
    (raw / "XRD").mkdir(parents=True)
    # settle time 0, no pop-ups from tests
    block = {"settle_minutes": 0, "notify": {"desktop": False}, **(automation or {})}
    (project / "config" / "local.yaml").write_text(
        f"raw_roots: [{{id: lab, path: '{raw.as_posix()}'}}]\nprotected_paths: []\n"
        f"automation: {json.dumps(block)}\n", encoding="utf-8")
    env = dict(os.environ, LABPIPE_PROJECT=str(project), PYTHONPATH=str(REPO / "src"))
    env.pop("LABPIPE_ROOT", None)
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
    (raw / "XRD" / "SYN-1_test.xrdml").write_text(xrdml_text(x, y, "SYN-1"), encoding="utf-8")
    (raw / "XRD" / "SYN-1_test.xy").write_text("\n".join(f"{a} {b}" for a, b in zip(x, y)), encoding="utf-8")
    (raw / "XRD" / "SYN-1_test.rd").write_bytes(b"\x00binary")
    (raw / "XRD" / "weird name.xrdml").write_text(xrdml_text(x, y), encoding="utf-8")
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
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    phase = results["phases"]["fcc_indexed"][0]
    assert phase["a_angstrom"] == pytest.approx(3.8898, abs=0.002)
    assert (project / "analysis" / "SYN-1" / "xrd" / "index.json").exists()

    # raw data untouched
    assert {p.name: p.read_bytes() for p in (raw / "XRD").iterdir()} == raw_before

    # a changed file is reprocessed; old outputs are superseded, not deleted
    time.sleep(0.01)
    x2, y2 = synthetic_pattern(peaks, seed=11)
    (raw / "XRD" / "SYN-1_test.xrdml").write_text(xrdml_text(x2, y2, "SYN-1"), encoding="utf-8")
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
                                     "phases: null\n", encoding="utf-8")
    shutil.copy(REPO / "config" / "plots.yaml", config / "plots.yaml")
    (config / "samples.yaml").write_text(
        "presets: {p1: {xrd: {background: {order: 3}, phases: [Pt]}}}\n"
        "samples: {S-1: {presets: [p1], xrd: {phases: [Pd]}}}\n", encoding="utf-8")
    (config / "measurements" / "xrd" / "S-1_a.yaml").write_text(
        "note: user asked on 2026-10-16\nbackground: {anchor_ranges_deg: [[30, 35]]}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG", config)
    monkeypatch.setattr(settings, "DEFAULTS", config)
    resolved, sources = settings.resolve("xrd", "S-1", "S-1_a")
    plots = resolved.pop("plots")      # the plot style, resolved on its own (plots.yaml)
    assert plots["preset"] == "acs"
    assert resolved == {"background": {"order": 3, "anchor_ranges_deg": [[30, 35]]},
                        "phases": ["Pd"]}
    assert [s["layer"].split()[0] for s in sources] == ["global", "sample", "measurement",
                                                        "plot"]
    (config / "measurements" / "xrd" / "S-1_a.yaml").write_text("backgruond: {order: 2}\n", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="backgruond"):
        settings.resolve("xrd", "S-1", "S-1_a")


def test_template_method_fulfils_the_contract(tmp_path):
    """The copy-and-adapt template must keep working (docs/ADDING_A_METHOD.md)."""
    import yaml
    from labpipe.methods.template import analyze as template
    data = tmp_path / "S-1_curve.txt"
    data.write_text("# x y\n" + "\n".join(f"{i} {i * (20 - i)}" for i in range(21)), encoding="utf-8")
    settings = yaml.safe_load((Path(template.__file__).parent / "config.example.yaml").read_text(encoding="utf-8"))
    summary = template.run(data, tmp_path / "out", settings=settings, pipeline=False,
                           context={"sample_id": "S-1", "measurement_id": "S-1_curve"})
    assert summary["status"] == "ok"
    assert set(summary["outputs"]) == {"results.json", "plot.png", "plot.pdf"}
    results = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert results["summary"]["x_at_y_max"] == 10.0
    record = json.loads((tmp_path / "out" / "provenance.json").read_text(encoding="utf-8"))
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
    (raw / "XRD" / "SYN-2_test.xrdml").write_text(xrdml_text(x, y, "SYN-2"), encoding="utf-8")
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
    steering.write_text("note: the report links S-13's images\nimages: [s-140.jpg, s-141.jpg]\n", encoding="utf-8")
    counts = _labpipe(env, "scan")["counts"]
    assert counts.get("pending") == 4 and "unrecognised" not in counts      # s-14x.jpg claimed
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
    (raw / "XRD" / "SYN-3_test.xrdml").write_text(xrdml_text(x, y, "SYN-3"), encoding="utf-8")
    _labpipe(env, "scan")
    before = _labpipe(env, "pending")[0]
    local = project / "config" / "local.yaml"
    local.write_text(local.read_text(encoding="utf-8").replace("id: lab", "id: renamed"), encoding="utf-8")
    assert _labpipe(env, "roots", "rename", "lab", "renamed")["renamed"] == 1
    assert _labpipe(env, "scan")["changes"] == []          # same files, nothing new
    after = _labpipe(env, "pending")[0]
    assert after["relative_path"] == before["relative_path"] and after["entry_id"] != before["entry_id"]


def test_automation_run_and_local_settings(tmp_path, monkeypatch):
    project, raw, env = _project(tmp_path)
    env["LABPIPE_AGENT_MODE"] = "unattended"
    assert _labpipe(env, "automation", "run")["status"] == "nothing_to_do"
    x, y = synthetic_pattern([(40.0, 5000.0, 1.0, 0.5)])
    (raw / "XRD" / "SYN-4_test.xrdml").write_text(xrdml_text(x, y, "SYN-4"), encoding="utf-8")
    # no agents configured -> the AI step cannot start; reported, not crashed
    result = _labpipe(env, "automation", "run")
    assert result["status"] == "run_failed" and "harness" in result["reason"]
    log = (project / "logs" / "automation.log").read_text(encoding="utf-8")
    assert '"event": "notify"' in log and "analysis started" in log

    monkeypatch.setattr("labpipe.automation.PROJECT_ROOT", project)
    monkeypatch.setattr("labpipe.safety.PROJECT_ROOT", project)
    from labpipe import automation
    from labpipe.safety import set_local_block
    local = project / "config" / "local.yaml"
    set_local_block("automation", {"settle_minutes": 3, "nightly_at": "02:00"})
    set_local_block("automation", {"settle_minutes": 5, "nightly_at": "03:30"})
    text = local.read_text(encoding="utf-8")
    assert text.count("automation:") == 1 and "settle_minutes: 5" in text and "raw_roots" in text


def test_notifications_tell_once_and_name_the_users_agent(tmp_path):
    from tests import test_sem
    project, raw, env = _project(tmp_path)
    with open(project / "config" / "local.yaml", "a") as stream:
        stream.write("agents: {harness: codex}\n")
    log = project / "logs" / "automation.log"

    def messages() -> list[str]:
        return [json.loads(line)["message"] for line in log.read_text(encoding="utf-8").splitlines()
                if '"event": "notify"' in line]

    (raw / "TEM").mkdir()
    (raw / "TEM" / "a.dm3").write_bytes(b"one")
    (raw / "Thumbs.db").write_bytes(b"x")              # system files: never reported
    (raw / "XRD" / "~$notes.docx").write_bytes(b"x")
    _labpipe(env, "automation", "run")
    [message] = messages()
    assert "1 new file(s) not recognised: TEM/a.dm3" in message
    assert "Open Codex in" in message and "say: check the new files" in message
    assert "TEM/a.dm3" in (project / "logs" / "review_queue.md").read_text(encoding="utf-8")
    _labpipe(env, "automation", "run")
    assert len(messages()) == 1                         # told once
    (raw / "TEM" / "a.dm3").write_bytes(b"two, changed")
    _labpipe(env, "automation", "run")
    assert len(messages()) == 2                         # new content: told again
    (project / "config" / "ignore.yaml").write_text('ignore: ["TEM/**"]\n', encoding="utf-8")
    assert _labpipe(env, "scan")["counts"] == {"ignored_by_you": 1, "system_file": 2}
    assert _labpipe(env, "status")["not_recognised"] == 0

    # code-only method (SEM): a broken image fails, a good one is done
    good = test_sem.make_example(raw / "SEM", "S-1 a001")
    bad = test_sem.make_example(raw / "SEM", "S-1 a002", mag=50000)
    bad.write_bytes(b"not a tiff")
    _labpipe(env, "automation", "run")
    failed = [m for m in messages() if "failed" in m]
    assert len(failed) == 1 and "SEM/S-1 a002.tif" in failed[0]
    assert "say: check the failed analyses" in failed[0]

    steering = project / "config" / "measurements" / "sem" / "S-1_a001.yaml"
    steering.parent.mkdir(parents=True, exist_ok=True)
    steering.write_text("info_bar_px: 60\n", encoding="utf-8")
    _labpipe(env, "automation", "run")
    [stale] = [m for m in messages() if "out of date" in m]
    assert "1 analysis(es) out of date (settings changed: 1" in stale
    assert "labpipe requeue --stale" in stale and good.name in stale


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
        (raw / "XRD" / f"SYN-{n}_test.xrdml").write_text(xrdml_text(x, y, f"SYN-{n}"), encoding="utf-8")
    (raw / "report.docx").write_bytes(b"not data")
    root = _labpipe(env, "inventory")["roots"][0]
    assert root["recognised"]["xrd"]["files"] == {".xrdml (analysed)": 3}
    assert root["not_recognised"] == {".docx": {"files": 1, "examples": ["report.docx"]}}
    assert root["already_in_manifest"] == 0

    # "only new files from now on": existing files are recorded, not queued
    assert _labpipe(env, "scan", "--baseline")["counts"] == {"baseline": 3, "unrecognised": 1}
    assert _labpipe(env, "scan")["changes"] == []
    assert _labpipe(env, "pending") == []
    (raw / "XRD" / "SYN-4_test.xrdml").write_text(xrdml_text(x, y, "SYN-4"), encoding="utf-8")
    assert _labpipe(env, "scan")["counts"] == {"baseline": 3, "pending": 1, "unrecognised": 1}

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
    monkeypatch.setattr("labpipe.container.container_mode", lambda: True)   # the watcher is in the container
    config = {"logs_dir": "logs", "manifest": "manifest.json", "automation": {}}
    log = tmp_path / "logs" / "automation.log"
    log.parent.mkdir()
    assert automation.health(config)["status"] == "ok"   # no run yet
    log.write_text(json.dumps({"utc": "2026-10-16T21:08:00Z", "event": "run_finished",
                               "exit_code": 0, "done": ["a"], "failed": [],
                               "cost_usd": 0.395}) + "\n", encoding="utf-8")
    report = automation.health(config)
    assert report["status"] == "ok" and "cost 0.40 USD" in report["message"]
    with open(log, "a") as stream:
        stream.write(json.dumps({"utc": "2026-10-17T02:00:00Z", "event": "scan_failed",
                                 "reason": "raw root not available"}) + "\n")
    report = automation.health(config)
    assert report["status"] == "warn" and "FAILED" in report["message"] and report["next"]
    (tmp_path / "manifest.json").write_text(json.dumps({"schema": "labpipe.manifest/1", "entries": {
        "x": {"entry_id": "x", "status": "pending",
              "events": [{"utc": "2026-10-10T00:00:00Z", "to": "pending"}]}}}), encoding="utf-8")
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
    (tmp_path / "a001.txt").write_text("$CM_FORMAT JEOL/SEM\n", encoding="utf-8")
    (tmp_path / "LSV.txt").write_text("Potential applied (V);Time (s)\n", encoding="utf-8")
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
    example.write_text(xrdml_text(x, y, "S-1"), encoding="utf-8")
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
    env = dict(os.environ, LABPIPE_CODE=str(project), LABPIPE_PROJECT=str(project),
               PYTHONPATH=str(REPO / "src"))      # code and project: the folder is its own project
    example = tmp_path / "scan.dat"
    example.write_text("1 2\n", encoding="utf-8")
    created = _labpipe(env, "methods", "new", "demo", "--example", str(example),
                       "--description", "Demo technique")["created"]
    for path in ("src/labpipe/methods/demo/analyze.py", "config/demo.yaml",
                 "agents/demo-analyst.md", "tests/test_demo.py", "docs/methods/demo.md"):
        assert path in created and (project / path).exists()
    analyze = (project / "src/labpipe/methods/demo/analyze.py").read_text(encoding="utf-8")
    assert 'METHOD = "demo"' in analyze and "labpipe.demo.results/1" in analyze
    role = (project / "agents/demo-analyst.md").read_text(encoding="utf-8")
    assert "name: demo-analyst" in role and "labpipe notes verify" in role and "{{" not in role
    import yaml
    entry = yaml.safe_load((project / "methods.yaml").read_text(encoding="utf-8"))["methods"]["demo"]
    assert entry["patterns"] == ["**/*.dat"] and entry["subagent"] == "demo-analyst"
    assert "already" in _labpipe(env, "methods", "new", "demo", "--example", str(example),
                                 "--description", "again")["error"]
    assert "name" in _labpipe(env, "methods", "new", "Bad-Name", "--example", str(example),
                              "--description", "x")["error"]


def test_watcher_sees_unrecognised_files_but_not_system_files(tmp_path):
    from labpipe import automation
    for name in ("a.dm3", "Thumbs.db", ".~lock.x.docx#"):
        (tmp_path / name).write_bytes(b"1")
    config = {"raw_roots": [{"id": "r", "path": str(tmp_path)}],
              "system_files": [".*", "Thumbs.db"]}
    assert set(automation._snapshot(config)) == {"r:a.dm3"}


@pytest.mark.skipif(sys.platform == "win32", reason="the fake agent tool is a shell script")
def test_a_failed_ai_run_is_reported_as_an_error(tmp_path):
    fake = tmp_path / "claude"
    project, raw, env = _project(tmp_path, automation={"claude": str(fake)})
    fake.write_text("#!/bin/sh\necho 'Warning: only a warning' >&2\necho 'Error: something broke' >&2\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    with open(project / "config" / "local.yaml", "a") as stream:
        stream.write("agents: {harness: claude-code}\n")
    x, y = synthetic_pattern([(40.0, 5000.0, 1.0, 0.5)])
    (raw / "XRD" / "SYN-6_test.xrdml").write_text(xrdml_text(x, y, "SYN-6"), encoding="utf-8")
    assert _labpipe(env, "automation", "run")["exit_code"] == 1
    last = json.loads([line for line in (project / "logs" / "automation.log").read_text(encoding="utf-8")
                       .splitlines() if '"event": "notify"' in line][-1])["message"]
    assert "AI run stopped with an error (Error: something broke)" in last
    assert "1 of 1 measurement(s) still waiting" in last and "Open Claude Code in" in last


def test_out_of_date_notifications_name_the_reason():
    from labpipe.automation import _stale_reasons
    stale = [{"status_reason": "the eds calculation changed (version a → b)"},
             {"status_reason": "analysed before per-method versions; redo to be sure"},
             {"status_reason": "analysis settings changed since the last run"}]
    assert _stale_reasons(stale) == {"calculation changed": 1, "code updated": 1,
                                     "settings changed": 1}


def test_two_moves_aside_in_one_second_do_not_collide(tmp_path):
    from labpipe.pipeline import _free
    target = tmp_path / "_superseded" / "m" / "20261022T101010Z"
    assert _free(target) == target
    target.mkdir(parents=True)
    assert _free(target).name == "20261022T101010Z-2"
    (target.parent / "20261022T101010Z-2").mkdir()
    assert _free(target).name == "20261022T101010Z-3"


def _two_scans(tmp_path):
    """Two different files with the same name in different folders, scanned and analysed."""
    project, raw, env = _project(tmp_path)
    positions = fcc_positions(3.8898)
    peaks = [(positions[i], h, 1.2 + 0.3 * i, 0.6) for i, h in enumerate((6000.0, 3000.0, 2200.0, 2000.0))]
    (raw / "XRD" / "old").mkdir()
    for name, seed in (("S-1_scan.xrdml", 3), ("old/S-1_scan.xrdml", 4)):
        x, y = synthetic_pattern(peaks, seed=seed)
        (raw / "XRD" / name).write_text(xrdml_text(x, y, "S-1"), encoding="utf-8")
    _labpipe(env, "scan")
    ids = {row["relative_path"]: row["entry_id"] for row in _labpipe(env, "pending")}
    results = {path: _labpipe(env, "xrd", "analyze", "--entry", eid) for path, eid in ids.items()}
    return project, raw, env, ids, results


def _manifest(project):
    return json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"]


def test_same_file_name_in_two_folders_gets_two_output_folders(tmp_path):
    project, raw, env, ids, results = _two_scans(tmp_path)
    assert {r["status"] for r in results.values()} == {"done"}
    first, second = (results[p]["output_dir"] for p in ("XRD/S-1_scan.xrdml", "XRD/old/S-1_scan.xrdml"))
    folders = sorted([first, second], key=len)
    assert folders[0] == "analysis/S-1/xrd/S-1_scan"
    digest = ids["XRD/old/S-1_scan.xrdml" if folders[1] == second else "XRD/S-1_scan.xrdml"].split("-")[1]
    assert folders[1] == f"analysis/S-1/xrd/S-1_scan~{digest[:6]}"
    for path, eid in ids.items():
        record = json.loads((project / results[path]["output_dir"] / "provenance.json").read_text(encoding="utf-8"))
        assert record["entry_id"] == eid
    assert not list((project / "analysis" / "S-1" / "xrd").rglob("_superseded"))

    # a rerun reuses the folder and supersedes only its own earlier outputs
    for path, eid in ids.items():
        again = _labpipe(env, "xrd", "analyze", "--entry", eid, "--rerun")
        assert again["output_dir"] == results[path]["output_dir"]
    assert len(list((project / "analysis" / "S-1" / "xrd").rglob("_superseded/*/*/results.json"))) == 2
    assert _labpipe(env, "scan")["changes"] == []


def test_publication_into_a_folder_of_another_entry_is_refused(tmp_path):
    project, raw, env, ids, results = _two_scans(tmp_path)
    a, b = ids["XRD/S-1_scan.xrdml"], ids["XRD/old/S-1_scan.xrdml"]
    entries = _manifest(project)
    victim = project / entries[b]["output_dir"]
    entries[b]["output_dir"] = entries[a]["output_dir"]       # b now points into a's folder
    (project / "manifest.json").write_text(
        json.dumps({"schema": "labpipe.manifest/1", "entries": entries}), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in (project / entries[a]["output_dir"]).iterdir()}
    result = _labpipe(env, "xrd", "analyze", "--entry", b, "--rerun")
    assert result["status"] == "failed" and "holds the analysis of XRD/S-1_scan.xrdml" in result["reason"]
    assert {p.name: p.read_bytes() for p in (project / entries[a]["output_dir"]).iterdir()} == before
    assert victim.is_dir()


def test_scan_separates_entries_that_share_an_output_folder(tmp_path):
    project, raw, env, ids, results = _two_scans(tmp_path)
    a, b = ids["XRD/S-1_scan.xrdml"], ids["XRD/old/S-1_scan.xrdml"]
    entries = _manifest(project)
    shared = entries[a]["output_dir"]
    entries[b]["output_dir"] = shared                          # a project from before the rule
    owner_file = project / shared / "provenance.json"
    record = json.loads(owner_file.read_text(encoding="utf-8"))
    del record["entry_id"]                                     # an old record names its input only
    owner_file.write_text(json.dumps(record), encoding="utf-8")
    (project / "manifest.json").write_text(
        json.dumps({"schema": "labpipe.manifest/1", "entries": entries}), encoding="utf-8")
    scan = _labpipe(env, "scan")
    assert [(c["entry_id"], c["to"]) for c in scan["changes"]] == [(b, "pending")]
    assert scan["changes"][0]["reason"] == ("shared an output folder with XRD/S-1_scan.xrdml; "
                                           "analysed again into its own folder")
    entries = _manifest(project)
    assert entries[a]["status"] == "done" and entries[a]["output_dir"] == shared
    assert entries[b]["output_dir"] == f"{shared}~{b.split('-')[1][:6]}"
    redone = _labpipe(env, "xrd", "analyze", "--entry", b)
    assert redone["status"] == "done" and redone["output_dir"] == entries[b]["output_dir"]
    assert (project / shared / "results.json").is_file()
    assert _labpipe(env, "scan")["changes"] == []


def test_a_moved_file_takes_its_output_folder_along(tmp_path):
    project, raw, env = _project(tmp_path)
    positions = fcc_positions(3.8898)
    peaks = [(positions[i], h, 1.2 + 0.3 * i, 0.6) for i, h in enumerate((6000.0, 3000.0, 2200.0, 2000.0))]
    x, y = synthetic_pattern(peaks, seed=3)
    (raw / "XRD" / "S-1_scan.xrdml").write_text(xrdml_text(x, y, "S-1"), encoding="utf-8")
    _labpipe(env, "scan")
    (first,) = _labpipe(env, "pending")
    done = _labpipe(env, "xrd", "analyze", "--entry", first["entry_id"])
    (raw / "XRD" / "2026-03").mkdir()
    (raw / "XRD" / "S-1_scan.xrdml").rename(raw / "XRD" / "2026-03" / "S-1_scan.xrdml")   # reorganised
    _labpipe(env, "scan")
    (moved,) = _labpipe(env, "pending")
    again = _labpipe(env, "xrd", "analyze", "--entry", moved["entry_id"])
    assert again["status"] == "done" and again["output_dir"] == done["output_dir"]   # no ~ folder
    entries = _manifest(project)
    assert entries[first["entry_id"]]["status"] == "missing"
    assert "output_dir" not in entries[first["entry_id"]]
    assert entries[first["entry_id"]]["output_moved_to"] == moved["entry_id"]
    assert sorted(p.name for p in (project / "analysis/S-1/xrd").iterdir()) == [
        "S-1_scan", "_superseded", "index.json"]                         # no ~ folder
    assert not [i for i in _labpipe(env, "doctor")["checks"] if i["check"] == "shared_output_dirs"]


def test_doctor_names_output_folders_shared_by_two_files(tmp_path):
    project, raw, env, ids, results = _two_scans(tmp_path)
    a, b = ids["XRD/S-1_scan.xrdml"], ids["XRD/old/S-1_scan.xrdml"]
    entries = _manifest(project)
    entries[b]["output_dir"] = entries[a]["output_dir"]
    (project / "manifest.json").write_text(
        json.dumps({"schema": "labpipe.manifest/1", "entries": entries}), encoding="utf-8")
    (item,) = [i for i in _labpipe(env, "doctor")["checks"] if i["check"] == "shared_output_dirs"]
    assert item["status"] == "warn" and entries[a]["output_dir"] in item["message"]
    assert "labpipe scan" in item["next"]


def test_report_cards_stay_unique_and_replay_works(tmp_path):
    project, raw, env, ids, results = _two_scans(tmp_path)
    page = (project / _labpipe(env, "report")["report"]).read_text(encoding="utf-8")
    import re
    anchors = re.findall(r'<section class=card id="([^"]+)"', page)
    assert len(anchors) == len(set(anchors)) and len(anchors) >= 2
    assert {r["output_dir"].rsplit("/", 1)[1] for r in results.values()} <= set(anchors)
    out = project / results["XRD/old/S-1_scan.xrdml"]["output_dir"]
    replay = _labpipe(env, "replay", str(out / "provenance.json"))
    assert replay["all_outputs_identical"], replay
