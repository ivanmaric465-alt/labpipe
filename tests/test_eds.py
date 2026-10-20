"""eds: a synthetic INCA report with a known composition, recognition, and the contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labpipe import settings as settings_mod
from labpipe.methods.eds import analyze, summary
from labpipe.methods_kit import check

REPORT = """<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">
<HTML><HEAD><TITLE>Project 1</TITLE></HEAD>
<P><IMG SRC="{stem}0.jpg">
<P>
Spectrum processing : <BR>
No peaks omitted<BR>
<BR>
Processing option : All elements analyzed (Normalised)<BR>
Number of iterations = 3<BR>
<BR>
Standard :<BR>
C    CaCO3   1-Jun-1999 12:00 AM<BR>
O    SiO2   1-Jun-1999 12:00 AM<BR>
Pd    Pd   1-Jun-1999 12:00 AM<BR>
Pt    Pt   1-Jun-1999 12:00 AM<BR>
<BR>
</P><TABLE BORDER=1>
<TR><TD>Element<TD>Weight%<TD>Atomic%<TD></TR>
<TR><TD>   <TD> <TD> <TD></TR>
<TR><TD>C K<TD>80,00<TD>88.00</TR>
<TR><TD>O K<TD>15.00<TD>11.40</TR>
<TR><TD>Pd L<TD>2.00<TD>0.40</TR>
<TR><TD>Pt M<TD>3.00<TD>0.20</TR>
<TR><TD><TD></TR>
<TR><TD>Totals<TD>100.00</TR>
</TABLE>
<P><IMG SRC="{stem}1.jpg">
<P>Comment:</P>
</HTML>
"""


def make_example(folder: Path, stem: str = "S-9") -> Path:
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / f"{stem}.htm"
    report.write_text(REPORT.format(stem=stem.upper()), encoding="latin-1")
    for n in (0, 1):   # INCA links upper case, files are lower case
        Image.new("RGB", (60, 40), (90 + 40 * n, 90, 90)).save(folder / f"{stem.lower()}{n}.jpg")
    return report


def test_eds_composition_and_metal_basis(tmp_path):
    report = make_example(tmp_path / "raw")
    out = tmp_path / "out"
    analyze.run(report, out, settings=None, context={"sample_id": "S-9",
                                                     "measurement_id": "S-9"}, pipeline=False)
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    elements = {e["element"]: e for e in results["composition"]["elements"]}
    assert elements["C"]["weight_percent"] == 80.0          # decimal comma read
    assert elements["Pt"]["line"] == "M"
    pd, pt = 2.00 / 106.42, 3.00 / 195.08
    assert results["metal_basis"]["at_percent"] == pytest.approx(
        {"Pd": 100 * pd / (pd + pt), "Pt": 100 * pt / (pd + pt)})
    assert results["metal_basis"]["at_percent_from_recorded_atomic_percent"] == pytest.approx(
        {"Pd": 400 / 6, "Pt": 200 / 6})
    assert [s["standard"] for s in results["standards"]] == ["CaCO3", "SiO2", "Pd", "Pt"]
    assert results["processing"]["iterations"] == 3
    assert [i["file"] for i in results["input"]["images"]] == ["s-90.jpg", "s-91.jpg"]
    codes = {c["code"] for c in results["checks"]}
    assert {"normalised_totals", "no_uncertainty_reported", "support_dominates",
            "no_nominal"} <= codes and "images_missing" not in codes
    assert results["status"] == "ok"


def test_eds_nominal_comparison_without_flag():
    measured = {"Pd": 60.0, "Pt": 40.0}
    comparison = analyze.compare_with_nominal(measured, {"Pd": 1, "Pt": 1})
    rows = {r["element"]: r for r in comparison["rows"]}
    assert rows["Pd"]["nominal_at_percent"] == 50.0
    assert rows["Pd"]["difference_at_percent"] == pytest.approx(10.0)
    assert rows["Pt"]["ratio"] == pytest.approx(0.8)
    assert analyze.compare_with_nominal(measured, None) is None


def test_eds_sigma_columns_are_uncertainty_not_composition():
    text = REPORT.format(stem="X").replace(
        "<TD>Element<TD>Weight%<TD>Atomic%<TD>", "<TD>Element<TD>Weight%<TD>Weight% Sigma<TD>Atomic%")
    text = text.replace("<TD>Pd L<TD>2.00<TD>0.40", "<TD>Pd L<TD>2.00<TD>0.30<TD>0.40")
    rows = {e["element"]: e for e in analyze.parse_report(text)["elements"]}
    assert rows["Pd"]["weight_percent"] == 2.0 and rows["Pd"]["weight_percent_sigma"] == 0.3
    assert rows["Pd"]["atomic_percent"] == 0.4


def test_eds_recognises_reports_and_their_images_only(tmp_path):
    report = make_example(tmp_path)
    (tmp_path / "photo.jpg").write_bytes((tmp_path / "s-90.jpg").read_bytes())
    (tmp_path / "page.htm").write_text("<html>hello</html>", encoding="utf-8")
    assert analyze.recognise(report) and analyze.recognise(tmp_path / "s-91.jpg")
    assert not analyze.recognise(tmp_path / "photo.jpg")
    assert not analyze.recognise(tmp_path / "page.htm")


def test_eds_summary_is_formatting_only(tmp_path):
    report = make_example(tmp_path / "raw")
    analyze.run(report, tmp_path / "out", context={}, pipeline=False)
    results = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    overview = summary.summarize(results)["overview"]
    assert overview["Metals (at%, metal basis)"] == "Pd 55.0 · Pt 45.0"
    assert overview["Nominal"] == "not given"


def test_eds_negative_value_is_taken_as_zero_and_flagged(tmp_path):
    report = make_example(tmp_path / "raw")
    text = report.read_text(encoding="latin-1").replace(
        "<TR><TD>Pt M<TD>3.00<TD>0.20</TR>",
        "<TR><TD>Pt M<TD>3.00<TD>0.20</TR>\n<TR><TD>Ru L<TD>-0.14<TD>-0.05</TR>")
    report.write_text(text, encoding="latin-1")
    out = tmp_path / "out"
    analyze.run(report, out, settings=settings_mod.resolve("eds", None, None)[0]
                | {"nominal_at_percent": {"Pd": 1, "Pt": 1, "Ru": 1}}, context={}, pipeline=False)
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    basis = results["metal_basis"]
    pd, pt = 2.00 / 106.42, 3.00 / 195.08
    assert basis["at_percent"] == pytest.approx(
        {"Pd": 100 * pd / (pd + pt), "Pt": 100 * pt / (pd + pt), "Ru": 0.0})
    assert sum(basis["at_percent"].values()) == pytest.approx(100.0)
    assert sum(basis["at_percent_from_recorded_atomic_percent"].values()) == pytest.approx(100.0)
    assert basis["at_percent_from_recorded_atomic_percent"]["Ru"] == 0.0
    assert basis["taken_as_zero"] == {"Ru": -0.14}
    ru = next(e for e in results["composition"]["elements"] if e["element"] == "Ru")
    assert ru["weight_percent"] == -0.14 and ru["atomic_percent"] == -0.05   # as recorded
    checks = {c["code"]: c for c in results["checks"]}
    assert checks["negative_value"]["severity"] == "review" and results["status"] == "review"
    assert "Ru −0.14 wt%" in checks["negative_value"]["message"]
    assert "low_concentration" not in checks   # a negative value is not "low"
    rows = {r["element"]: r for r in results["nominal_comparison"]["rows"]}
    assert rows["Ru"]["measured_at_percent"] == 0.0
    assert rows["Pd"]["measured_at_percent"] == pytest.approx(basis["at_percent"]["Pd"])
    overview = summary.summarize(results)["overview"]
    assert "Ru n.d. (negative)" in overview["Metals (at%, metal basis)"]
    assert "Ru -0.1" in overview["All elements (wt%)"]
    assert any(c["code"] == "negative_value" for c in summary.summarize(results)["check_before_using"])


def test_eds_contract(tmp_path):
    result = check("eds", make_example(tmp_path / "raw"), work_dir=tmp_path / "check")
    assert result["ok"], [c for c in result["checks"] if not c["ok"]]


def test_eds_flags_images_named_for_another_report(tmp_path):
    make_example(tmp_path, "S-13")
    other = tmp_path / "S-14.htm"
    other.write_text(REPORT.format(stem="S-13"), encoding="latin-1")   # links S-130/131.jpg
    (tmp_path / "s-140.jpg").write_bytes((tmp_path / "s-130.jpg").read_bytes())
    summary = analyze.run(other, tmp_path / "out", context={}, pipeline=False)
    [flag] = [c for c in summary["checks"] if c["code"] == "linked_images_other_name"]
    assert "S-130.jpg" in flag["message"] and "s-140.jpg" in flag["message"]


def test_eds_images_from_settings_replace_the_links(tmp_path):
    make_example(tmp_path, "S-13")
    other = tmp_path / "S-14.htm"
    other.write_text(REPORT.format(stem="S-13"), encoding="latin-1")
    for n in (0, 1):
        (tmp_path / f"s-14{n}.jpg").write_bytes((tmp_path / f"s-13{n}.jpg").read_bytes())
    assert analyze.recognise(tmp_path / "s-140.jpg")      # named for a report, not linked
    settings = settings_mod.resolve("eds", None, None)[0] | {"images": ["s-140.jpg", "s-141.jpg"]}
    out = tmp_path / "out"
    summary = analyze.run(other, out, settings=settings, context={}, pipeline=False)
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    codes = {c["code"] for c in results["checks"]}
    assert "images_from_settings" in codes and "linked_images_other_name" not in codes
    assert [i["file"] for i in results["input"]["images"]] == ["s-140.jpg", "s-141.jpg"]
    assert results["input"]["images_source"] == "settings" and summary["status"] == "ok"
    assert (out / "panel_electron_image.jpg").read_bytes() == (tmp_path / "s-140.jpg").read_bytes()
    assert [f["file"] for f in results["figures"]] == [
        "panel_electron_image.jpg", "panel_spectrum.jpg", "panel_composition.png"]
    assert set(summary["outputs"]) >= {f["file"] for f in results["figures"]}
