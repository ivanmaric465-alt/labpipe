"""The XRD comparison figures (labpipe.methods.xrd.compare): patterns from the raw files and
the lattice parameter per phase."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("matplotlib")

from labpipe import compare, comparekinds, versions  # noqa: E402
from labpipe.methods.xrd import compare as xrd_compare  # noqa: E402
from tests.conftest import xrdml_text  # noqa: E402


def _results(raw_bytes, a=3.89, sigma=0.002, ref="Pd", lines=(40.1, 46.6, 68.1)):
    return {"input": {"sha256": hashlib.sha256(raw_bytes).hexdigest(),
                      "relative_path": "XRD/x.xrdml", "format": "xrdml"},
            "units": {"size": "nm"}, "checks": [], "size_strain": [],
            "phases": {
                "fcc_indexed": [{"id": "fcc-1", "a_angstrom": a, "a_sigma_angstrom": sigma,
                                 "n_reflections": 3, "tentative": False,
                                 "reference_comparison": [
                                     {"phase": ref, "delta_a_percent": 0.1,
                                      "a_reference_angstrom": 3.8898},
                                     {"phase": "Pt", "delta_a_percent": 1.0,
                                      "a_reference_angstrom": 3.9231}]}],
                "reference_matches": [
                    {"phase": ref, "is_candidate": True,
                     "matched": [{"two_theta_deg": v} for v in lines[:2]],
                     "unmatched": [{"two_theta_deg": lines[2]}]},
                    {"phase": "Pt", "is_candidate": False, "matched": [],
                     "unmatched": [{"two_theta_deg": 39.8}]}]}}


def _measurement(tmp_path, sample, mid, x, y, **kw):
    path = tmp_path / f"{mid}.xrdml"
    path.write_text(xrdml_text(x, y), encoding="utf-8")
    raw = path.read_bytes()
    return {"sample": sample, "entry_id": f"xrd:lab:{mid}", "measurement_id": mid,
            "status": "done", "results": _results(raw, **kw),
            "results_path": f"analysis/{sample}/xrd/{mid}/results.json",
            "results_sha256": "r" * 64, "output_dir": f"analysis/{sample}/xrd/{mid}",
            "input": _results(raw)["input"], "raw_path": str(path), "raw_root": "lab"}


@pytest.fixture
def pair(tmp_path):
    x = np.linspace(10.0, 80.0, 5001)
    y = 100.0 + 5000.0 * np.exp(-((x - 40.1) / 0.3) ** 2) + 3000.0 * np.exp(-((x - 46.6) / 0.3) ** 2)
    y = np.round(y)
    return [_measurement(tmp_path, "S-1", "S-1_a", x, y),
            _measurement(tmp_path, "S-2", "S-2_a", x, y * 0.5, a=3.9, sigma=0.003)], x, y


CONTEXT = {"plot": {}, "config": {"plot": {"y_scale": "sqrt"}, "input": {"scan_index": 0}}}


def _by_name(figs):
    return {f["name"]: f for f in figs}


def test_two_figures_with_the_recorded_data_and_covers(pair):
    measurements, x, y = pair
    figs = _by_name(xrd_compare.figures(measurements, CONTEXT))
    assert set(figs) == {"patterns", "lattice-fcc"} and xrd_compare.COVERS == ["xrd.a.*"]
    p = figs["patterns"]
    assert p["kind"] == "lines" and p["y_scale"] == "sqrt" and p["x_label"] == "2θ (°)"
    assert [t["measurement_id"] for t in p["traces"]] == ["S-1_a", "S-2_a"]
    assert p["tick_rows"] == [{"label": "Pd (reference)", "positions": [40.1, 46.6, 68.1]}]
    assert p["traces"][0]["source"]["sha256"] == measurements[0]["input"]["sha256"]
    d = figs["lattice-fcc"]
    assert d["kind"] == "dots" and [q["value"] for q in d["points"]] == [3.89, 3.9]
    assert [q["sigma"] for q in d["points"]] == [0.002, 0.003]
    assert d["reference_lines"] == [{"value": 3.8898, "label": "Pd"}]   # candidates only
    for spec in figs.values():
        comparekinds.module(spec["kind"]).check(json.loads(json.dumps(spec)))


def test_deterministic(pair):
    measurements, _, _ = pair
    first = xrd_compare.figures(measurements, CONTEXT)
    again = xrd_compare.figures(copy.deepcopy(measurements), CONTEXT)
    assert json.dumps(first) == json.dumps(again)


def test_thinning_keeps_every_peak_maximum(pair):
    measurements, x, y = pair
    trace = _by_name(xrd_compare.figures(measurements, CONTEXT))["patterns"]["traces"][0]
    assert len(trace["x"]) <= xrd_compare.MAX_POINTS < len(x)
    assert max(trace["y"]) == y.max() and trace["x"] == sorted(trace["x"])
    near = [v for u, v in zip(trace["x"], trace["y"]) if abs(u - 46.6) < 0.5]
    assert max(near) == y[np.abs(x - 46.6) < 0.5].max()
    assert "buckets" in trace["thinning"]


def test_short_patterns_are_not_thinned(tmp_path):
    x = np.linspace(10.0, 80.0, 300)
    m = _measurement(tmp_path, "S-1", "S-1_a", x, np.arange(300.0))
    trace = xrd_compare.figures([m], CONTEXT)[0]["traces"][0]
    assert len(trace["x"]) == 300 and trace["thinning"].startswith("none")


def test_changed_or_missing_raw_file_leaves_the_measurement_out_with_a_reason(pair):
    measurements, x, y = pair
    open(measurements[0]["raw_path"], "a").write(" ")
    kept = measurements[1]["raw_path"]
    measurements[1]["raw_path"] = None
    figs = _by_name(xrd_compare.figures(measurements, CONTEXT))
    assert "patterns" not in figs and "lattice-fcc" in figs     # the lattice does not need the file
    measurements[1]["raw_path"] = kept
    patterns = _by_name(xrd_compare.figures(measurements, CONTEXT))["patterns"]
    assert [t["measurement_id"] for t in patterns["traces"]] == ["S-2_a"]
    assert patterns["skipped"][0]["measurement_id"] == "S-1_a"
    assert "differs from the one analysed" in patterns["caption"] and "S-1_a" in patterns["caption"]


def test_no_candidates_means_no_tick_rows_and_no_reference_lines(pair):
    measurements, _, _ = pair
    for m in measurements:
        for row in m["results"]["phases"]["reference_matches"]:
            row["is_candidate"] = False
    figs = _by_name(xrd_compare.figures(measurements, CONTEXT))
    assert figs["patterns"]["tick_rows"] == [] and figs["lattice-fcc"]["reference_lines"] == []


def test_the_module_is_in_the_compare_version_only():
    assert "labpipe.methods.xrd.compare" not in versions.describe("xrd")["analysis_modules"]
    assert "labpipe.methods.xrd.compare" in versions.compare_version()["compare_modules"]


def test_figures_draw_and_redraw_identically(pair, tmp_path):
    from labpipe import settings as settings_mod
    plot, _ = settings_mod.plot_style()
    for spec in xrd_compare.figures(pair[0], CONTEXT):
        valid = compare._valid(spec, "xrd", plot)
        out = []
        for name in ("a", "b"):
            folder = tmp_path / name / spec["name"]
            folder.mkdir(parents=True)
            compare.render(valid, folder / "plot")
            out.append(folder)
        for f in ("plot.png", "figure.html"):
            assert (out[0] / f).read_bytes() == (out[1] / f).read_bytes()
        assert compare.sources(valid)         # the raw file / results.json are checked on replay


def test_a_raw_source_is_recorded_by_its_data_root_not_an_absolute_path(tmp_path, pair):
    m = pair[0][0]
    trace, _ = xrd_compare._trace(m, {})
    assert trace["source"] == {"raw_root": "lab", "path": "XRD/x.xrdml",
                               "sha256": m["input"]["sha256"]}
    assert str(tmp_path) not in json.dumps(trace)
    assert compare.sources({"traces": [trace]}) == {"raw:lab/XRD/x.xrdml": m["input"]["sha256"]}
    config = {"raw_roots": [{"id": "lab", "path": str(tmp_path / "data")}]}
    assert compare._source_file("raw:lab/XRD/x.xrdml", config) == tmp_path / "data/XRD/x.xrdml"
    assert compare._source_file("raw:other/x", config) is None
