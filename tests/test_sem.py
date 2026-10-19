"""sem: synthetic JEOL image + .txt with known metadata, gallery choice, and the contract."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from labpipe.methods.sem import analyze, gallery
from labpipe.methods_kit import check

JEOL = """$CM_FORMAT TIFF\r
$CM_DATE 5/14/2026\r
$CM_TIME 11:59:05 AM\r
$CM_INSTRUMENT JSM-7000F\r
$CM_ACCEL_VOLT 10.0\r
$CM_MAG {mag}\r
$CM_SIGNAL SEI\r
$$SM_WD 4.0\r
$$SM_MICRON_BAR 117\r
$$SM_MICRON_MARKER {marker}\r
$CM_IMAGE_RES 1280x1024\r
$CM_SCAN_SPEED 173\r
$CM_STAGE_POSITION X=37.5390, Y=24.4637, R=0.0959, Z=4.55, T=0.00\r
"""


def make_example(folder: Path, name: str = "S-10 a001", mag: int = 110000,
                 marker: str = "100nm") -> Path:
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    pixels = rng.integers(60, 200, size=(1024, 1280), dtype=np.uint8)
    pixels[960:, :] = 0                      # JEOL info bar
    image = folder / f"{name}.tif"
    Image.fromarray(pixels, mode="L").save(image)
    (folder / f"{name}.txt").write_bytes(JEOL.format(mag=mag, marker=marker).encode())
    return image


def test_sem_metadata_and_pixel_size(tmp_path):
    image = make_example(tmp_path / "raw")
    out = tmp_path / "out"
    summary = analyze.run(image, out, context={"sample_id": "S-10",
                                               "measurement_id": "S-10_a001"}, pipeline=False)
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert summary["status"] == "ok" and (out / "thumb.jpg").exists()
    assert results["acquisition"]["magnification"] == 110000
    assert results["acquisition"]["accelerating_voltage_kV"] == 10.0
    assert results["acquisition"]["working_distance_mm"] == 4.0
    assert results["scale"]["pixel_size_nm"] == pytest.approx(100 / 117)
    assert results["scale"]["field_of_view_um"]["width"] == pytest.approx(1280 * 100 / 117 / 1000)
    assert results["scale"]["field_of_view_um"]["height"] == pytest.approx(960 * 100 / 117 / 1000)
    assert (results["series"], results["index"]) == ("a", 1)


def test_sem_micrometre_bar_and_unreadable_bar(tmp_path):
    image = make_example(tmp_path / "raw", "S-1 b002", mag=10000, marker="1µm")
    out = tmp_path / "out"
    analyze.run(image, out, context={}, pipeline=False)
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["scale"]["pixel_size_nm"] == pytest.approx(1000 / 117)
    bad = make_example(tmp_path / "raw2", "S-1 b003", marker="??")
    summary = analyze.run(bad, tmp_path / "out2", context={}, pipeline=False)
    assert [c["code"] for c in summary["checks"]] == ["scale_bar_unreadable"]


def test_sem_recognises_only_jeol_files(tmp_path):
    image = make_example(tmp_path)
    (tmp_path / "other.tif").write_bytes(image.read_bytes())
    (tmp_path / "LSV.txt").write_text("Potential applied (V);Time (s)\n", encoding="utf-8")
    assert analyze.recognise(image) and analyze.recognise(image.with_suffix(".txt"))
    assert not analyze.recognise(tmp_path / "other.tif")
    assert not analyze.recognise(tmp_path / "LSV.txt")


def _item(measurement: str, mag: int, series: str, index: int) -> dict:
    return {"entry": {"measurement_id": measurement},
            "results": {"acquisition": {"magnification": mag}, "series": series, "index": index}}


def test_gallery_closest_on_log_scale_across_series():
    items = [_item("a001", 9000, "a", 1), _item("a002", 70000, "a", 2),
             _item("b001", 30000, "b", 1), _item("b002", 130000, "b", 2),
             _item("b003", 11000, "b", 3)]
    # 10k: ln(11/10) < ln(10/9) -> b003; 50k: ln(70/50) < ln(50/30) -> a002;
    # 100k: ln(130/100) < ln(100/70) -> b002. Sorted by magnification.
    chosen = gallery.select(items, {"magnifications": [10000, 50000, 100000]})
    assert [c["entry"]["measurement_id"] for c in chosen] == ["b003", "a002", "b002"]
    edited = gallery.select(items, {"magnifications": [50000], "exclude": ["a002"],
                                    "include": ["a001"]})
    assert [c["entry"]["measurement_id"] for c in edited] == ["a001", "b001"]


def test_sem_contract(tmp_path):
    result = check("sem", make_example(tmp_path / "raw"), work_dir=tmp_path / "check")
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]
