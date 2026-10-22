"""The EDS composition bars and the SEM gallery grid (comparison hooks): fixed order and
colours, nominal only where it exists, the grid picks the gallery's images, replay."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("matplotlib")

from labpipe import compare, dataset, registry, replay, report, versions  # noqa: E402
from labpipe.registry import load_methods as REAL_METHODS  # noqa: E402
from labpipe import settings as settings_mod  # noqa: E402
from labpipe.methods.eds import compare as eds_compare  # noqa: E402
from labpipe.methods.eds import summary as eds_summary  # noqa: E402
from labpipe.methods.sem import compare as sem_compare  # noqa: E402
from labpipe.methods.sem import gallery  # noqa: E402
from labpipe.provenance import sha256_file  # noqa: E402
from tests.test_compare import CONFIG, FOLDER, _eds, _put, project  # noqa: E402,F401


def _m(sample, mid, results, **extra):
    return {"sample": sample, "entry_id": f"eds:{mid}", "measurement_id": mid,
            "results": results, "results_path": f"a/{mid}/results.json",
            "results_sha256": "h", "output_dir": f"a/{mid}", **extra}


def _eds_results(at, nominal=None):
    rows = None if nominal is None else {"rows": [
        {"element": e, "nominal_at_percent": v, "difference_at_percent": 0.0}
        for e, v in nominal.items()]}
    return {"metal_basis": {"at_percent": at, "excluded_non_metals": ["O"]},
            "nominal_comparison": rows}


# --- EDS -------------------------------------------------------------------------------------

def test_eds_bars_elements_by_atomic_number_with_fixed_colours():
    ms = [_m("S-1", "S-1_a", _eds_results({"Pt": 60.0, "Ru": 40.0})),
          _m("S-2", "S-2_a", _eds_results({"Ti": 10.0, "Pt": 50.0, "Pd": 40.0}))]
    (spec,) = eds_compare.figures(ms, {})
    assert spec["kind"] == "bars" and spec["name"] == "composition"
    assert [c["name"] for c in spec["categories"]] == ["Ti", "Ru", "Pd", "Pt"]
    assert {c["name"]: c["color"] for c in spec["categories"]} == {
        e: eds_compare.COLOURS[e] for e in ("Ti", "Ru", "Pd", "Pt")}
    assert [s["category"] for s in spec["rows"][0]["segments"]] == ["Ru", "Pt"]
    assert spec["rows"][1]["segments"][0] == {"category": "Ti", "value": 10.0}
    assert eds_compare.figures(ms, {}) == [spec]                       # deterministic
    json.dumps(spec)


def test_eds_colours_are_distinct_and_the_fallback_is_by_atomic_number():
    common = "Ti Fe Co Ni Cu Ru Rh Pd Ag Ir Pt Au".split()
    assert len({eds_compare.COLOURS[e] for e in common}) == len(common)
    assert eds_compare.colour("Zr") == eds_compare.colour("Zr")
    assert eds_compare.colour("Zr") in eds_compare.FALLBACK


def test_eds_nominal_only_with_a_nominal():
    ms = [_m("S-1", "S-1_a", _eds_results({"Pd": 70.0, "Pt": 30.0}, {"Pd": 75.0, "Pt": 25.0})),
          _m("S-2", "S-2_a", _eds_results({"Pd": 70.0, "Pt": 30.0}))]
    rows = eds_compare.figures(ms, {})[0]["rows"]
    assert rows[0]["nominal_segments"] == [{"category": "Pd", "value": 75.0},
                                           {"category": "Pt", "value": 25.0}]
    assert rows[1]["nominal_segments"] is None


def test_eds_covers_the_at_quantities_only():
    from fnmatch import fnmatchcase
    assert any(fnmatchcase("eds.at.Pt", g) for g in eds_compare.COVERS)
    assert not any(fnmatchcase("eds.at_minus_nominal.Pt", g) for g in eds_compare.COVERS)
    assert eds_compare.figures([], {}) == []


def test_eds_quantity_nominal_only_when_given():
    q = eds_summary.quantities(_eds_results({"Pd": 70.0, "Pt": 30.0}, {"Pd": 75.0}))
    assert q["eds.at.Pd"]["nominal"] == 75.0 and "nominal" not in q["eds.at.Pt"]
    assert "nominal" not in eds_summary.quantities(_eds_results({"Pd": 1.0}))["eds.at.Pd"]


# --- SEM -------------------------------------------------------------------------------------

def _sem_item(root, sample, mid, mag, thumb=True):
    out = root / "analysis" / sample / "sem" / mid
    out.mkdir(parents=True, exist_ok=True)
    if thumb:
        from PIL import Image
        Image.new("L", (40, 30), 90 + mag % 100).save(out / "thumb.jpg")
    return _m(sample, mid, {"series": "a", "index": 1, "acquisition": {"magnification": mag}},
              output_dir=f"analysis/{sample}/sem/{mid}")


@pytest.fixture
def sem(tmp_path, monkeypatch):
    monkeypatch.setattr(sem_compare, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings_mod, "resolve",
                        lambda *a, **k: ({"gallery": {"magnifications": [10000, 50000],
                                                      "exclude": ["S-1_c"]}}, None))
    return tmp_path


CONTEXT = {"config": {"gallery": {"magnifications": [10000, 50000], "include": [],
                                  "exclude": []}}}


def test_sem_grid_picks_what_the_gallery_picks(sem):
    ms = [_sem_item(sem, "S-1", "S-1_a", 12000), _sem_item(sem, "S-1", "S-1_b", 55000),
          _sem_item(sem, "S-1", "S-1_c", 10000),             # excluded: never chosen
          _sem_item(sem, "S-2", "S-2_a", 50000)]
    (spec,) = sem_compare.figures(ms, CONTEXT)
    assert spec["kind"] == "images" and spec["name"] == "gallery"
    assert [c["label"] for c in spec["columns"]] == ["×10,000", "×50,000"]
    assert [r["sample"] for r in spec["rows"]] == ["S-1", "S-2"]
    cells = spec["rows"][0]["cells"]
    items = [{"entry": {"measurement_id": m["measurement_id"]}, "results": m["results"]}
             for m in ms[:3]]
    picked = gallery.select(items, {"magnifications": [10000, 50000], "exclude": ["S-1_c"]})
    assert {cells["10000"]["measurement_id"], cells["50000"]["measurement_id"]} == {
        p["entry"]["measurement_id"] for p in picked} == {"S-1_a", "S-1_b"}
    cell = cells["10000"]
    assert cell["path"] == "analysis/S-1/sem/S-1_a/thumb.jpg"
    assert cell["sha256"] == sha256_file(sem / cell["path"])
    assert cell["label"] == "×12,000 · S-1_a"
    assert sem_compare.figures(ms, CONTEXT) == [spec]


def test_sem_empty_cell_without_an_image(sem):
    ms = [_sem_item(sem, "S-1", "S-1_a", 12000), _sem_item(sem, "S-2", "S-2_a", 50000, thumb=False)]
    spec = sem_compare.figures(ms, CONTEXT)[0]
    assert spec["rows"][1]["cells"] == {}                 # no thumb.jpg: "–" in the grid
    assert sem_compare.figures([], CONTEXT) == []
    assert sem_compare.figures(ms, {"config": {"gallery": {"magnifications": []}}}) == []


# --- through the framework ---------------------------------------------------------------------

def test_eds_figure_through_compare_covers_and_replays(project, monkeypatch):
    for mod in (registry, compare, report, dataset, versions):
        monkeypatch.setattr(mod, "load_methods", REAL_METHODS)
    result = compare.build(CONFIG)
    done = {(f["method"], f["name"]): f for f in result["figures"]}
    assert ("eds", "composition") in done and done[("eds", "composition")]["status"] == "drawn"
    assert not [k for k in done if k[0] == "eds" and k[1].startswith("eds.at.")]
    path = project / FOLDER / "eds" / "composition" / "provenance.json"
    again = replay.replay(path, CONFIG)
    assert again["all_outputs_identical"] and again["problems"] == []


def test_sem_now_has_a_figure_so_the_report_setting_names_it(project, monkeypatch):
    for mod in (registry, compare, report, dataset, versions):
        monkeypatch.setattr(mod, "load_methods", REAL_METHODS)
    (project / "config" / "lab.yaml").write_text(
        "report: {compare: {sem: [gallery], eds: [composition, eds.at.Pt]}}\n", encoding="utf-8")
    assert report.report_settings()["report"]["compare"]["sem"] == ["gallery"]
