"""dataset: named quantities of xrd and eds, the project table, difference and trend."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from labpipe import cli, dataset, manifest, safety
from labpipe import settings as settings_mod
from labpipe.methods.eds import summary as eds_summary
from labpipe.methods.xrd import summary as xrd_summary

CONFIG = {"manifest": "manifest.json", "analysis_dir": "analysis", "logs_dir": "logs"}


def xrd_results(a=3.8870, sigma=0.0035, size=5.55, uncalibrated=True):
    """The parts of an xrd results.json that quantities() reads (shape as in a real one)."""
    return {
        "units": {"size": "nm"},
        "internal_standard": {"phase": "Si", "lattice_a_angstrom": 5.4311},
        "phases": {"fcc_indexed": [
            {"id": "fcc-1", "a_angstrom": a, "a_sigma_angstrom": sigma, "tentative": False,
             "n_reflections": 3,
             "reference_comparison": [{"phase": "Pd", "delta_a_percent": -0.07}]},
            {"id": "fcc-2", "a_angstrom": 4.05, "a_sigma_angstrom": None, "tentative": True,
             "n_reflections": 2, "reference_comparison": []}]},
        "size_strain": [{"phase": "fcc-1", "instrument_correction": "none (no instrument "
                         "calibration)", "scherrer": [
                             {"hkl": [1, 1, 1], "size_nm": size, "size_sigma_nm_from_fwhm": 0.034},
                             {"hkl": [2, 0, 0], "size_nm": 5.0, "size_sigma_nm_from_fwhm": 0.05}]}],
        "checks": ([{"code": "no_instrument_correction", "severity": "warning"}]
                   if uncalibrated else [])}


def eds_results(pt=23.66, nominal=True):
    results = {"units": {"metal_basis.at_percent": "at% of the metals (sum 100)",
                         "difference_at_percent": "at% points (metal basis)"},
               "metal_basis": {"at_percent": {"Pd": 76.34, "Pt": pt}},
               "nominal_comparison": None}
    if nominal:
        results["nominal_comparison"] = {"rows": [
            {"element": "Pd", "difference_at_percent": -3.66},
            {"element": "Pt", "difference_at_percent": 3.66},
            {"element": "Ru", "difference_at_percent": None}]}
    return results


def test_xrd_quantities_by_phase_id():
    q = xrd_summary.quantities(xrd_results())
    assert set(q) == {"xrd.a.fcc-1", "xrd.a.fcc-2", "xrd.size.fcc-1"}   # no standard
    assert q["xrd.a.fcc-1"] == {"value": 3.8870, "sigma": 0.0035, "unit": "Å",
                                "key": "phases.fcc_indexed[0].a_angstrom",
                                "note": "closest reference Pd"}
    assert q["xrd.a.fcc-2"]["sigma"] is None and "tentative (2 reflections)" in q["xrd.a.fcc-2"]["note"]
    size = q["xrd.size.fcc-1"]
    assert (size["value"], size["sigma"], size["unit"]) == (5.55, 0.034, "nm")
    assert size["key"] == "size_strain[0].scherrer[0].size_nm"
    assert "lower bound until an instrument calibration exists" in size["note"]


def test_xrd_size_without_lower_bound_note_when_calibrated():
    results = xrd_results(uncalibrated=False)
    results["size_strain"][0]["instrument_correction"] = "Si standard"
    assert "lower bound" not in xrd_summary.quantities(results)["xrd.size.fcc-1"]["note"]


def test_eds_quantities():
    q = eds_summary.quantities(eds_results())
    assert q["eds.at.Pt"] == {"value": 23.66, "sigma": None,
                              "unit": "at% of the metals (sum 100)",
                              "key": "metal_basis.at_percent.Pt"}
    assert q["eds.at_minus_nominal.Pt"]["value"] == 3.66
    assert q["eds.at_minus_nominal.Pt"]["key"] == "nominal_comparison.rows[1].difference_at_percent"
    assert "eds.at_minus_nominal.Ru" not in q                      # no value, no quantity
    assert not [k for k in eds_summary.quantities(eds_results(nominal=False))
                if k.startswith("eds.at_minus")]


# --- the project table -------------------------------------------------------------------

def _entry(eid, method, sample, mid, status, results=None, **extra):
    entry = {"entry_id": eid, "method": method, "sample_id": sample, "measurement_id": mid,
             "status": status, "relative_path": f"{method}/{mid}"}
    if results is not None:
        out = f"analysis/{sample}/{method}/{mid}"
        entry |= {"output_dir": out, "results_sha256": "abc"}
        folder = safety.PROJECT_ROOT / out
        folder.mkdir(parents=True)
        (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
        (folder / "plot.png").write_bytes(b"png")
    return entry | extra


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (dataset, manifest, safety, settings_mod):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    entries = [
        _entry("xrd-1", "xrd", "S-2", "S-2_a", "done", xrd_results(), review=True,
               review_codes=["wh_unreliable"]),
        _entry("eds-1", "eds", "S-2", "S-2_e1", "done", eds_results()),
        _entry("eds-2", "eds", "S-2", "S-2_e2", "stale", eds_results(pt=25.0)),
        _entry("xrd-2", "xrd", "S-10", "S-10_a", "failed", status_reason="boom"),
        _entry("xrd-3", "xrd", "S-1", "S-1_a", "done", xrd_results(a=3.9)),
        _entry("sem-1", "sem", "S-1", "S-1_i", "done", {"schema": "x"}),
        _entry("xrd-4", "xrd", "S-1", "S-1_dup", "duplicate"),
        _entry("xrd-5", "xrd", None, "scan", "needs_review"),
    ]
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"schema": manifest.SCHEMA, "updated_utc": None,
         "entries": {e["entry_id"]: e for e in entries}}), encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "samples.yaml").write_text(
        "samples:\n  S-1: {eds: {nominal_at_percent: {Pd: 80, Pt: 20}}, "
        "properties: {loading_wt: 20, support: carbon}}\n  S-3: {}\n", encoding="utf-8")
    (tmp_path / "analysis" / "S-2" / "xrd" / "S-2_a" / "notes.md").write_text("n", encoding="utf-8")
    return tmp_path


def test_build_shape_order_and_gaps(project):
    data = dataset.build(CONFIG)
    assert data["schema"] == "labpipe.dataset/1" and data["generated_utc"]
    assert list(data["samples"]) == ["?", "S-1", "S-2", "S-3", "S-10"]   # natural order
    s1 = data["samples"]["S-1"]
    assert {m["entry_id"] for m in s1["measurements"]} == {"xrd-3"}   # duplicate skipped
    assert s1["properties"]["sample.nominal.Pt"] == {
        "value": 20.0, "sigma": None, "unit": "at%",
        "source": "config/samples.yaml → samples.S-1.eds.nominal_at_percent.Pt"}
    assert s1["properties"]["sample.loading_wt"]["source"].endswith("samples.S-1.properties.loading_wt")
    assert "sample.support" not in s1["properties"]                    # not a number
    # image methods: one summary per sample, not one row per image
    assert s1["galleries"] == {"sem": {"images": 1, "statuses": {"done": 1},
                                       "plots": ["analysis/S-1/sem/S-1_i/plot.png"]}}
    xrd = next(m for m in data["samples"]["S-2"]["measurements"] if m["method"] == "xrd")
    assert xrd["notes"] == "analysis/S-2/xrd/S-2_a/notes.md" and xrd["review_codes"] == ["wh_unreliable"]
    assert "xrd.a.fcc-1" in xrd["quantities"] and xrd["results_sha256"] == "abc"
    failed = data["samples"]["S-10"]["measurements"][0]
    assert (failed["status"], failed["quantities"], failed["notes"]) == ("failed", {}, None)
    assert data["samples"]["S-3"]["measurements"] == []                # only in samples.yaml
    assert [m["status"] for m in data["samples"]["?"]["measurements"]] == ["needs_review"]


def test_compare_on_a_built_project(project):
    out = dataset.compare(CONFIG, "xrd.a.fcc-1", reference="S-1")
    assert [p["sample"] for p in out["points"]] == ["S-2"]
    assert out["points"][0]["difference"] == pytest.approx(3.8870 - 3.9)
    assert {"sample": "S-10", "reason": "failed"} in out["left_out"]
    assert {"sample": "S-3", "reason": "no xrd measurement"} in out["left_out"]
    assert out["points"][0]["review_codes"] == ["wh_unreliable"]       # kept, flagged
    # S-2 has two eds measurements: never picked silently
    several = dataset.compare(CONFIG, "eds.at.Pt", against="sample.nominal.Pt")
    assert {"sample": "S-2", "reason": "several measurements: choose --measurement or --each"} \
        in several["left_out"]
    each = dataset.compare(CONFIG, "eds.at.Pt", reference="S-2", measurements=["eds-1"])
    assert each["reference_point"]["entry_id"] == "eds-1"
    with pytest.raises(ValueError, match="several measurements"):
        dataset.compare(CONFIG, "eds.at.Pt", reference="S-2")
    with pytest.raises(ValueError, match="no such measurement"):
        dataset.compare(CONFIG, "eds.at.Pt", reference="S-2", measurements=["nope"])


# --- difference and trend against hand-computed values ----------------------------------------

def _data(samples):
    """samples: {id: {"props": {name: v}, "m": [(entry_id, status, {quantity: (value, sigma)}, codes)]}}"""
    return {"schema": "labpipe.dataset/1", "generated_utc": "t", "samples": {
        sample: {"properties": {n: {"value": v, "sigma": None, "unit": "wt%", "source": "s"}
                                for n, v in spec.get("props", {}).items()},
                 "measurements": [
                     {"entry_id": eid, "method": "xrd", "measurement_id": f"{eid}-m",
                      "status": status, "review_codes": codes,
                      "quantities": {q: {"value": v, "sigma": s, "unit": "Å", "key": "k"}
                                     for q, (v, s) in qs.items()}}
                     for eid, status, qs, codes in spec.get("m", [])]}
        for sample, spec in samples.items()}}


A = "xrd.a.fcc-1"


def test_difference_values_and_sigma():
    data = _data({"S-1": {"m": [("e1", "done", {A: (3.900, 0.003)}, [])]},
                  "S-2": {"m": [("e2", "done", {A: (3.890, 0.004)}, [])]},
                  "S-3": {"m": [("e3", "done", {A: (3.920, None)}, [])]}})
    out = dataset.compare(CONFIG, A, reference="S-1", dataset=data)
    by = {p["sample"]: p for p in out["points"]}
    # S-2: 3.890 - 3.900 = -0.010; σ = sqrt(0.003² + 0.004²) = sqrt(25e-6) = 0.005
    assert by["S-2"]["difference"] == pytest.approx(-0.010)
    assert by["S-2"]["difference_sigma"] == pytest.approx(0.005)
    # S-3: 3.920 - 3.900 = 0.020; no σ on one side, so none
    assert by["S-3"]["difference"] == pytest.approx(0.020) and by["S-3"]["difference_sigma"] is None
    assert out["operation"] == "difference" and out["reference"] == "S-1" and "S-1" not in by


def test_trend_matches_hand_computation():
    # x = 0, 1, 2, 3 and y = 1, 3, 2, 5: mean x 1.5, mean y 2.75
    # Sxx = 5, Sxy = 5.5 -> slope 1.1, intercept 2.75 - 1.1 * 1.5 = 1.1
    # fitted 1.1, 2.2, 3.3, 4.4 -> residuals -0.1, 0.8, -1.3, 0.6 -> SSR = 2.7
    # SST = 8.75 -> R² = 1 - 2.7 / 8.75; s² = 2.7 / 2 = 1.35
    # σ_slope = sqrt(1.35 / 5) = sqrt(0.27); σ_intercept = sqrt(1.35 * (1/4 + 2.25/5)) = sqrt(0.945)
    ys = [1, 3, 2, 5]
    data = _data({f"S-{i}": {"props": {"sample.loading": float(i)},
                             "m": [(f"e{i}", "done", {A: (ys[i], 0.01)}, [])]} for i in range(4)})
    out = dataset.compare(CONFIG, A, against="sample.loading", dataset=data)
    r = out["result"]
    assert r["n"] == 4
    assert r["slope"] == pytest.approx(1.1) and r["intercept"] == pytest.approx(1.1)
    assert r["r_squared"] == pytest.approx(1 - 2.7 / 8.75)
    assert r["slope_sigma"] == pytest.approx(math.sqrt(0.27))
    assert r["intercept_sigma"] == pytest.approx(math.sqrt(0.945))
    assert out["method"] == "ordinary least squares, unweighted; point σ not used as weights"
    assert out["points"][1]["x"] == 1.0 and out["points"][1]["y"] == 3
    assert out["units"]["against"] == "wt%"


def test_trend_with_two_points_has_no_standard_errors():
    data = _data({f"S-{i}": {"props": {"sample.loading": float(i)},
                             "m": [(f"e{i}", "done", {A: (2.0 * i + 1, None)}, [])]}
                  for i in range(2)})
    r = dataset.compare(CONFIG, A, against="sample.loading", dataset=data)["result"]
    assert (r["slope"], r["intercept"]) == (pytest.approx(2.0), pytest.approx(1.0))
    assert r["slope_sigma"] is None and "at least 3" in r["reason"]


def test_several_measurements_are_never_picked_silently():
    data = _data({
        "S-1": {"props": {"sample.loading": 1.0}, "m": [("a", "done", {A: (1.0, None)}, []),
                                                       ("b", "done", {A: (2.0, None)}, [])]},
        "S-2": {"props": {"sample.loading": 2.0}, "m": [("c", "done", {A: (3.0, None)}, [])]},
        "S-3": {"props": {"sample.loading": 3.0}, "m": [("d", "done", {A: (4.0, None)}, [])]}})
    out = dataset.compare(CONFIG, A, against="sample.loading", dataset=data)
    assert out["left_out"] == [{"sample": "S-1",
                                "reason": "several measurements: choose --measurement or --each"}]
    assert out["result"]["n"] == 2
    picked = dataset.compare(CONFIG, A, against="sample.loading", measurements=["b"], dataset=data)
    assert [p["y"] for p in picked["points"] if p["sample"] == "S-1"] == [2.0]
    both = dataset.compare(CONFIG, A, against="sample.loading", each=True, dataset=data)
    assert [p["y"] for p in both["points"] if p["sample"] == "S-1"] == [1.0, 2.0]
    assert both["left_out"] == [] and both["result"]["n"] == 4
    # by measurement_id as well as entry_id
    by_id = dataset.compare(CONFIG, A, against="sample.loading", measurements=["a-m"], dataset=data)
    assert [p["y"] for p in by_id["points"] if p["sample"] == "S-1"] == [1.0]


def test_left_out_reasons_and_flags():
    data = _data({
        "S-1": {"m": [("a", "done", {A: (1.0, None)}, [])]},
        "S-2": {"m": [("b", "stale", {A: (2.0, None)}, ["wh_unreliable"])]},
        "S-3": {"m": [("c", "failed", {}, [])]},
        "S-4": {"m": [("d", "pending", {}, [])]},
        "S-5": {"m": [("e", "done", {"xrd.a.fcc-2": (4.0, None)}, [])]},
        "S-6": {"m": []}})
    out = dataset.compare(CONFIG, A, reference="S-1", dataset=data)
    assert [p["sample"] for p in out["points"]] == ["S-2"]
    assert out["points"][0]["stale"] is True and out["points"][0]["review_codes"] == ["wh_unreliable"]
    reasons = {x["sample"]: x["reason"] for x in out["left_out"]}
    assert reasons == {"S-3": "failed", "S-4": "not analysed (status: pending)",
                       "S-5": f"no such quantity in its xrd results ({A})",
                       "S-6": "no xrd measurement"}


def test_exactly_one_of_reference_and_against():
    with pytest.raises(ValueError):
        dataset.compare(CONFIG, A, dataset=_data({}))
    with pytest.raises(ValueError):
        dataset.compare(CONFIG, A, reference="S-1", against="x", dataset=_data({}))


# --- saving and the command ----------------------------------------------------------------

def test_save_numbers_files_from_one(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "PROJECT_ROOT", tmp_path)
    run = tmp_path / "analysis" / "_postdoc" / "2026-10-19T10-00-00Z"
    run.mkdir(parents=True)
    first = dataset.save(run, {"a": 1}, CONFIG)
    second = dataset.save(run, {"a": 2}, CONFIG)
    assert (first.name, second.name) == ("compare-1.json", "compare-2.json")
    assert json.loads(second.read_text(encoding="utf-8")) == {"a": 2}
    with pytest.raises(safety.UnsafePathError):
        dataset.save(tmp_path / "elsewhere", {"a": 1}, CONFIG)


def test_cli_compare_saves_into_a_postdoc_run(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PROJECT_ROOT", project)
    run = project / "analysis" / "_postdoc" / "r1"
    run.mkdir(parents=True)
    args = SimpleNamespace(action="compare", quantity="xrd.a.fcc-1", reference="S-1",
                           against=None, measurement=None, each=False,
                           run="analysis/_postdoc/r1")
    assert cli.cmd_dataset(args, CONFIG) == 0
    assert json.loads(capsys.readouterr().out)["saved_as"] == "analysis/_postdoc/r1/compare-1.json"
    assert (run / "compare-1.json").exists()
    args.run = "analysis/S-1"
    assert cli.cmd_dataset(args, CONFIG) == 1
    assert "must be an existing folder" in json.loads(capsys.readouterr().out)["error"]
