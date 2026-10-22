"""The figure kinds of the comparison figures: deterministic, marked for the report's script."""

from __future__ import annotations

import math
import re

import pytest

pytest.importorskip("matplotlib")

from labpipe import compare, safety  # noqa: E402
from labpipe import comparekinds as ck  # noqa: E402
from labpipe.provenance import sha256_file  # noqa: E402

PLOT = {"font": "Arial", "font_size": 9, "label_size": 10, "line_width": 1.0, "palette": "okabe-ito",
        "single_column_in": 3.25, "double_column_in": 7.0, "dpi": 100, "formats": ["png", "pdf"],
        "preset": "acs", "compare": compare.DEFAULT_STYLE}
PAIRS = [("S-1", "S-1_a"), ("S-2", "S-2_a"), ("S-2", "S-2_b"), ("S-3", 'S-3_"q"')]


def dots():
    points = [{"sample": s, "entry_id": f"x:{m}", "measurement_id": m, "value": 3.9 + 0.01 * i,
               "sigma": None if s == "S-3" else 0.004, "nominal": 3.9 if s == "S-1" else None,
               "unit": "Å", "status": "done", "results_path": "r.json", "results_sha256": "0",
               "key": "k"} for i, (s, m) in enumerate(PAIRS)]
    return {"kind": "dots", "method": "xrd", "name": "a", "title": "a", "label": "a (Å)", "unit": "Å",
            "samples": ["S-1", "S-2", "S-3"], "points": points,
            "reference_lines": [{"value": 3.92, "label": "Pt"}], "plot": PLOT}


def lines():
    traces = [{"sample": s, "measurement_id": m, "entry_id": f"x:{m}",
               "x": [30 + k * 0.5 for k in range(80)],
               "y": [10 + 100 * math.exp(-((30 + k * 0.5 - 40 - i) / 1.5) ** 2) for k in range(80)],
               "source": {"path": "raw", "sha256": "0"}} for i, (s, m) in enumerate(PAIRS)]
    return {"kind": "lines", "method": "xrd", "name": "p", "title": "patterns", "x_label": "2θ (°)",
            "y_label": "Intensity", "traces": traces, "y_scale": "sqrt", "layout": "stacked",
            "tick_rows": [{"label": "Pt", "positions": [40.0, 46.5]}], "plot": PLOT}


def bars():
    cats = [{"name": n, "color": c} for n, c in (("Pt", "#0072B2"), ("Pd", "#D55E00"), ("Ir", "#009E73"))]
    rows = [{"sample": s, "measurement_id": m, "entry_id": f"x:{m}",
             "segments": [{"category": "Pt", "value": 60.0 - i}, {"category": "Pd", "value": 35.0},
                          {"category": "Ir", "value": 5.0 + i}],
             "nominal_segments": [{"category": "Pt", "value": 50.0}, {"category": "Pd", "value": 50.0}]
             if i == 0 else None, "results_path": "r.json", "results_sha256": "0"}
            for i, (s, m) in enumerate(PAIRS)]
    return {"kind": "bars", "method": "eds", "name": "c", "title": "composition",
            "unit_label": "at%", "categories": cats, "rows": rows, "plot": PLOT}


@pytest.fixture
def images(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(safety, "PROJECT_ROOT", tmp_path)
    cells = {}
    for i, (s, m) in enumerate([PAIRS[0], PAIRS[1], ("S-3", "S-3_a")]):
        Image.effect_noise((640, 480), 30 + i * 30).save(tmp_path / f"{m}.png")
        cells.setdefault(s, {})["500"] = {"measurement_id": m, "entry_id": f"x:{m}",
                                          "path": f"{m}.png", "sha256": sha256_file(tmp_path / f"{m}.png"),
                                          "label": f"{m} ×500"}
    cells["S-2"]["500"]["sha256"] = "changed"                       # said in the cell
    cells["S-1"]["5000"] = {"measurement_id": "S-1_a", "entry_id": "x", "path": "gone.png",
                            "sha256": "0", "label": "missing"}
    return {"kind": "images", "method": "sem", "name": "g", "title": "grid",
            "columns": [{"key": "500", "label": "×500"}, {"key": "5000", "label": "×5000"}],
            "rows": [{"sample": s, "cells": cells.get(s, {})} for s in ("S-1", "S-2", "S-3")],
            "plot": PLOT}


@pytest.fixture(params=["dots", "lines", "bars", "images"])
def spec(request):
    return request.getfixturevalue("images") if request.param == "images" else globals()[request.param]()


def test_every_kind_is_registered_and_checked(spec):
    kind = ck.module(spec["kind"])
    kind.check(spec)
    assert set(ck.KINDS) == {"dots", "lines", "bars", "images"}
    assert kind.caption(spec) and kind.inventory(spec)
    with pytest.raises(ValueError, match="unknown figure kind"):
        ck.module("pie")


def test_same_spec_same_bytes_png_pdf_and_html(spec, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir(), second.mkdir()
    one = compare.render(spec, first / "plot")
    compare.render(spec, second / "plot")
    assert one["files"] == ["plot.png", "plot.pdf", "figure.html"]
    for name in one["files"]:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    page = (first / "figure.html").read_text(encoding="utf-8")
    assert page == ck.module(spec["kind"]).html(spec, compare.fig_id(spec)) + "\n"


def test_marks_carry_measurement_and_sample_and_controls_exist(spec):
    page = ck.module(spec["kind"]).html(spec, "cf-x")
    kind = ck.module(spec["kind"])
    marks = set(re.findall(r'data-m="([^"]*)" data-s="([^"]*)"', page))
    assert marks == {(ck.esc(m), ck.esc(s)) for s, m in kind.inventory(spec)}
    boxes = re.findall(r'<input type=checkbox checked data-cm="([^"]*)" data-cs="([^"]*)"', page)
    assert {(m, s) for m, s in boxes} == marks
    assert "data-all=1>All" in page and "data-all=0>None" in page
    assert '<div class=cf id="cf-x"' in page and "<script" not in page
    assert '"q"' not in page.replace("&quot;q&quot;", "")                     # escaped
    assert "viewBox" in page or spec["kind"] == "images"


def test_one_measurement_samples_are_a_single_box_several_are_grouped():
    page = ck.module("dots").html(dots(), "f")
    assert page.count("<input") == 4
    assert ">S-2</span>" in page and "S-2_a</label>" in page and "S-1_a</label>" not in page


def test_dots_style_one_dark_colour_sigma_only_where_given_nominal_and_references():
    page = ck.module("dots").html(dots(), "f")
    assert page.count("<circle") == 4 and page.count("class=eb") == 3      # S-3 has no σ
    assert page.count("class=nm") == 1 and page.count("class=rf") == 1 and ">Pt</text>" in page
    assert "fill=\"#" not in page.split("<div class=cl>")[0].split("</style>")[1]      # theme colours only
    assert "no σ, so no bar, for S-3_" in ck.module("dots").caption(dots())


def test_lines_have_four_views_and_the_stacked_ones_move_as_groups():
    page = ck.module("lines").html(lines(), "f")
    assert [m for m in re.findall(r'<g class=v data-v="(\w+)"', page)] == ["s0", "s1", "o0", "o1"]
    assert page.count("<polyline") == 16 and page.count("data-i=") == 8
    assert 'data-view=s aria-pressed=true>Stacked' in page and "Normalised to maximum" in page
    assert re.search(r'<g class=v data-v="s0">', page) and 'data-v="o0" style=display:none' in page
    assert len(set(re.findall(r'stroke="(#[0-9A-Fa-f]{6})"', page))) == 4        # one colour per trace
    assert "#F0E442" not in page and ">Pt</text>" in page


def test_bars_print_values_from_8_percent_and_label_the_nominal():
    page = ck.module("bars").html(bars(), "f")
    assert ">60.0</text>" in page and ">5.0</text>" not in page and ">nominal</text>" in page
    assert page.count("<title>Pt: 50 (nominal)") == 1


def test_images_say_what_is_wrong_instead_of_failing(images):
    page = ck.module("images").html(images, "f")
    assert page.count("<img") == 2 and "image changed" in page and "image missing" in page
    assert "<div class=na>–</div>" in page
    assert re.search(r'width="320"', page)
    assert page == ck.module("images").html(images, "f")
