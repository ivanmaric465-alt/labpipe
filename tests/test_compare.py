"""Comparison figures across samples: drawn from the named quantities, provenance, replay,
settings and the report section. Needs matplotlib like the other plot tests."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytest.importorskip("matplotlib")

from labpipe import automation, cli, compare, dataset, manifest, postdoc, replay, report, safety  # noqa: E402
from labpipe import settings as settings_mod  # noqa: E402
from labpipe import versions  # noqa: E402
from labpipe.provenance import sha256_file  # noqa: E402
from tests import test_report  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CONFIG = {"raw_roots": [], "protected_paths": [], "analysis_dir": "analysis",
          "logs_dir": "logs", "manifest": "manifest.json", "agents": {}}
FOLDER = "analysis/_compare"


def _put(root, entries, method, sample, mid, results, status="done"):
    out = f"analysis/{sample}/{method}/{mid}"
    (root / out).mkdir(parents=True, exist_ok=True)
    (root / out / "results.json").write_text(json.dumps(results), encoding="utf-8")
    (root / out / "plot.png").write_bytes(b"IMG")
    entries[f"{method}:lab:{mid}"] = {
        "entry_id": f"{method}:lab:{mid}", "method": method, "sample_id": sample,
        "measurement_id": mid, "status": status, "output_dir": out,
        "relative_path": f"{mid}.raw", "results_sha256": "x"}


def _xrd(a, sigma=0.0035, with_size=False):
    results = test_report.xrd_results([test_report.phase(a=a, sigma=sigma)])
    if not with_size:
        results["size_strain"] = []
    return results


def _eds(pt):
    at = {"Pd": 100.0 - pt, "Pt": pt}
    return test_report.eds_results(at, at, non_metals=())


def without_own_figures(methods):
    """The real registry minus the methods' own comparison figures: these tests are about the
    generic dot plots (the XRD figures are tested in test_xrd_compare.py)."""
    return {name: {k: v for k, v in spec.items() if k != "compare"} for name, spec in methods.items()}


@pytest.fixture
def project(tmp_path, monkeypatch):
    from labpipe import registry, versions as versions_mod
    for module in (compare, report, dataset, versions_mod):
        monkeypatch.setattr(module, "load_methods", lambda: without_own_figures(registry.load_methods()))
    for module in (safety, manifest, postdoc, report, automation, dataset, compare, settings_mod):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    for name in ("report.yaml", "plots.yaml"):
        shutil.copy(REPO / "config" / name, config / name)
    monkeypatch.setattr(settings_mod, "CONFIG", config)
    monkeypatch.setattr(settings_mod, "DEFAULTS", config)
    monkeypatch.setattr(report, "verify_entry", lambda config, eid: {"ok": True})
    entries: dict = {}
    # xrd: S-2 measured twice, S-3 without σ; only S-1 has a size
    _put(tmp_path, entries, "xrd", "S-10", "S-10_a", _xrd(3.92))
    _put(tmp_path, entries, "xrd", "S-2", "S-2_a", _xrd(3.88))
    _put(tmp_path, entries, "xrd", "S-2", "S-2_b", _xrd(3.89, sigma=0.002))
    _put(tmp_path, entries, "xrd", "S-1", "S-1_a", _xrd(3.90, with_size=True))
    _put(tmp_path, entries, "xrd", "S-3", "S-3_a", _xrd(3.91, sigma=None))
    for sample, pt in (("S-1", 20.0), ("S-2", 25.0), ("S-3", 30.0)):
        _put(tmp_path, entries, "eds", sample, f"{sample}_e", _eds(pt))
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"schema": manifest.SCHEMA, "updated_utc": None, "entries": entries}), encoding="utf-8")
    (tmp_path / "logs").mkdir()
    return tmp_path


def lab(project, text):
    (project / "config" / "lab.yaml").write_text(text, encoding="utf-8")


def provenance(project, method, quantity):
    path = project / FOLDER / method / quantity / "provenance.json"
    return json.loads(path.read_text(encoding="utf-8"))


def figures(result):
    return {(f["method"], f["name"]): f for f in result["figures"]}


# --- what is drawn -------------------------------------------------------------------------------

def test_figures_for_quantities_with_two_samples_and_skips_with_reasons(project):
    result = compare.build(CONFIG)
    drawn = figures(result)
    assert set(drawn) == {("xrd", "xrd.a.fcc"), ("eds", "eds.at.Pd"), ("eds", "eds.at.Pt")}
    assert all(f["status"] == "drawn" for f in drawn.values())
    assert {(s["name"], s["reason"]) for s in result["skipped"]} == {
        ("xrd.size.fcc", "only 1 sample")}
    for (method, quantity), figure in drawn.items():
        folder = project / FOLDER / method / quantity
        assert {p.name for p in folder.iterdir()} == {"plot.png", "plot.pdf", "figure.html",
                                                       "provenance.json"}
        assert figure["png"] == f"{FOLDER}/{method}/{quantity}/plot.png"
    assert result["moved_aside"] == [] and result["unmatched"] == []


def test_samples_in_the_reports_sample_order_with_one_point_per_measurement(project):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    spec = prov["spec"]
    assert spec["kind"] == "dots" and spec["samples"] == ["S-1", "S-2", "S-3", "S-10"]  # natural
    assert [(p["sample"], p["measurement_id"]) for p in spec["points"]] == [
        ("S-1", "S-1_a"), ("S-2", "S-2_a"), ("S-2", "S-2_b"), ("S-3", "S-3_a"), ("S-10", "S-10_a")]
    (project / "config" / "samples.yaml").write_text(
        "samples:\n  S-10: {}\n  S-3: {}\n", encoding="utf-8")
    lab(project, "report: {sample_order: samples}\n")
    result = compare.build(CONFIG)
    assert figures(result)[("xrd", "xrd.a.fcc")]["status"] == "redrawn"      # the order is an input
    assert provenance(project, "xrd", "xrd.a.fcc")["spec"]["samples"] == [
        "S-10", "S-3", "S-1", "S-2"]


def test_dot_plots_have_no_per_sample_styles(project):
    compare.build(CONFIG)
    spec = provenance(project, "xrd", "xrd.a.fcc")["spec"]
    assert not any("color" in str(k) or "marker" in str(k) for k in spec)
    assert all(set(p) >= {"nominal", "sigma"} for p in spec["points"])
    html = (project / FOLDER / "xrd" / "xrd.a.fcc" / "figure.html").read_text(encoding="utf-8")
    assert html.count("<circle") == 5 and 'data-m="S-2_b"' in html and 'data-s="S-2"' in html


def test_no_sigma_points_and_the_caption_says_so(project):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    assert [p["sigma"] for p in prov["spec"]["points"]] == [0.0035, 0.0035, 0.002, None, 0.0035]
    assert "no σ, so no bar, for S-3_a" in prov["caption"]
    assert "Error bars: σ from the fit" in prov["caption"]
    assert "S-2 ×2" in prov["caption"] and "5 measurements of 4 samples" in prov["caption"]
    assert "No σ for any point, so no error bars" in provenance(project, "eds", "eds.at.Pt")["caption"]


def test_provenance_holds_the_data_the_hashes_and_the_code(project):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    assert prov["schema"] == "labpipe.compare-figure/2"
    assert (prov["method"], prov["name"], prov["kind"], prov["spec"]["unit"]) == (
        "xrd", "xrd.a.fcc", "dots", "Å")
    point = prov["spec"]["points"][0]
    assert point["results_path"] == "analysis/S-1/xrd/S-1_a/results.json"
    assert point["results_sha256"] == sha256_file(project / point["results_path"])
    assert (point["entry_id"], point["key"], point["value"]) == (
        "xrd:lab:S-1_a", "phases.fcc_indexed[0].a_angstrom", 3.90)
    assert prov["spec"]["plot"]["compare"]["palette"] == "okabe-ito"
    assert prov["spec"]["plot"]["dpi"] == 300
    assert prov["code"]["compare_version"] == versions.compare_version()["compare_version"]
    assert prov["code"]["labpipe_entry"] == "labpipe.compare.render" and "commit" in prov["code"]
    folder = project / FOLDER / "xrd" / "xrd.a.fcc"
    assert prov["outputs"] == {name: sha256_file(folder / name)
                               for name in ("figure.html", "plot.pdf", "plot.png")}
    assert prov["replay"] == f"labpipe replay {FOLDER}/xrd/xrd.a.fcc/provenance.json"
    assert prov["runtime"]["mode"] in ("native", "container")


def test_the_same_inputs_give_the_same_bytes(project, tmp_path):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    again = tmp_path / "again"
    again.mkdir()
    compare.render(prov["spec"], again / "plot")
    for name in ("plot.png", "plot.pdf", "figure.html"):
        assert (again / name).read_bytes() == (project / FOLDER / "xrd" / "xrd.a.fcc" / name).read_bytes()


def test_kept_when_nothing_changed_redrawn_with_the_old_one_moved_aside(project):
    compare.build(CONFIG)
    png = project / FOLDER / "xrd" / "xrd.a.fcc" / "plot.png"
    before, stamp = png.stat().st_mtime_ns, provenance(project, "xrd", "xrd.a.fcc")["created_utc"]
    result = compare.build(CONFIG)
    assert {f["status"] for f in result["figures"]} == {"kept"}
    assert png.stat().st_mtime_ns == before and not (project / FOLDER / "_superseded").exists()
    # a data change: only the figures of that quantity are redrawn
    path = project / "analysis" / "S-1" / "xrd" / "S-1_a" / "results.json"
    path.write_text(json.dumps(_xrd(3.95, with_size=True)), encoding="utf-8")
    result = compare.build(CONFIG)
    status = {q: f["status"] for (m, q), f in figures(result).items()}
    assert status == {"xrd.a.fcc": "redrawn", "eds.at.Pd": "kept", "eds.at.Pt": "kept"}
    old = list((project / FOLDER / "_superseded").glob("*/xrd/xrd.a.fcc/provenance.json"))
    assert len(old) == 1 and json.loads(old[0].read_text(encoding="utf-8"))["created_utc"] == stamp
    assert provenance(project, "xrd", "xrd.a.fcc")["spec"]["points"][0]["value"] == 3.95
    # a plot style change redraws the figures too; a figure edited by hand is redrawn
    lab(project, "plots: {compare: {marker_size: 6}}\n")
    assert {f["status"] for f in compare.build(CONFIG)["figures"]} == {"redrawn"}
    png.write_bytes(b"edited")
    assert figures(compare.build(CONFIG))[("xrd", "xrd.a.fcc")]["status"] == "redrawn"


def test_a_compare_version_change_redraws(project, monkeypatch):
    compare.build(CONFIG)
    now = versions.compare_version()
    monkeypatch.setattr(versions, "compare_version", lambda: now | {"compare_version": "other"})
    assert {f["status"] for f in compare.build(CONFIG)["figures"]} == {"redrawn"}


def test_a_quantity_that_no_longer_exists_is_moved_aside_not_deleted(project):
    compare.build(CONFIG)
    data = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    data["entries"]["eds:lab:S-2_e"]["status"] = "failed"
    data["entries"]["eds:lab:S-3_e"]["status"] = "failed"
    (project / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    result = compare.build(CONFIG)
    assert {(m["method"], m["name"]) for m in result["moved_aside"]} == {
        ("eds", "eds.at.Pd"), ("eds", "eds.at.Pt")}
    assert not (project / FOLDER / "eds" / "eds.at.Pt").exists()
    moved = project / result["moved_aside"][0]["to"]
    assert (moved / "plot.png").exists() and (moved / "provenance.json").exists()
    assert [s["name"] for s in result["skipped"] if s["method"] == "eds"] == [
        "eds.at.Pd", "eds.at.Pt"]


def test_method_option_limits_what_is_built(project):
    result = compare.build(CONFIG, method="eds")
    assert {f["method"] for f in result["figures"]} == {"eds"}
    assert not (project / FOLDER / "xrd").exists()
    with pytest.raises(ValueError, match="unknown method"):
        compare.build(CONFIG, method="nope")


def test_stale_analyses_are_used_and_said(project):
    data = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    data["entries"]["xrd:lab:S-2_a"]["status"] = "stale"
    (project / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    compare.build(CONFIG)
    assert "1 point is from an analysis that is stale" in provenance(
        project, "xrd", "xrd.a.fcc")["caption"]


# --- replay --------------------------------------------------------------------------------------

def test_replay_from_the_provenance_alone_is_identical(project):
    compare.build(CONFIG)
    path = project / FOLDER / "xrd" / "xrd.a.fcc" / "provenance.json"
    result = replay.replay(path, CONFIG)
    assert result["all_outputs_identical"] and result["problems"] == [] and result["notes"] == []
    assert set(result["comparison"]) == {"plot.png", "plot.pdf", "figure.html"}
    assert (project / result["replay_dir"] / "out" / "plot.png").exists()


def test_replay_notes_a_changed_source_and_does_not_fail(project):
    compare.build(CONFIG)
    (project / "analysis" / "S-1" / "xrd" / "S-1_a" / "results.json").write_text(
        json.dumps(_xrd(4.2)), encoding="utf-8")
    shutil.rmtree(project / "analysis" / "S-3")
    result = replay.replay(project / FOLDER / "xrd" / "xrd.a.fcc" / "provenance.json", CONFIG)
    assert result["all_outputs_identical"]
    assert any("S-1_a/results.json has changed" in n for n in result["notes"])
    assert any("S-3_a/results.json is no longer there" in n for n in result["notes"])


def test_replay_needs_the_same_compare_version(project, monkeypatch):
    compare.build(CONFIG)
    path = project / FOLDER / "xrd" / "xrd.a.fcc" / "provenance.json"
    now = versions.compare_version()
    monkeypatch.setattr(versions, "compare_version", lambda: now | {"compare_version": "other"})
    refused = replay.replay(path, CONFIG)
    assert refused["error"] == "replay refused" and "compare version differs" in refused["problems"][0]
    assert replay.replay(path, CONFIG, force=True)["all_outputs_identical"]


def test_a_different_commit_is_only_noted(project, monkeypatch):
    compare.build(CONFIG)
    path = project / FOLDER / "xrd" / "xrd.a.fcc" / "provenance.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["code"]["commit"] = "0" * 40
    path.write_text(json.dumps(record), encoding="utf-8")
    result = replay.replay(path, CONFIG)
    assert result["problems"] == [] and "made with commit 000" in result["notes"][0]


# --- settings ------------------------------------------------------------------------------------

def test_defaults_name_the_section_and_compare_all():
    defaults = yaml.safe_load((REPO / "config" / "report.yaml").read_text(encoding="utf-8"))
    assert defaults["sections"] == ["postdoc", "compare", "overview", "measurements"]
    assert defaults["compare"] == "all"


@pytest.mark.parametrize("text, message", [
    ("report: {compare: [xrd]}\n", "compare must be 'all' or a mapping"),
    ("report: {compare: {nope: [x.a]}}\n", "compare: unknown method 'nope'"),
    ("report: {compare: {sem: true}}\n", "method 'sem' names no quantities"),
    ("report: {compare: {xrd: [eds.at.Pt]}}\n", "'eds.at.Pt' is not a xrd quantity"),
    ("report: {compare: {xrd: 3}}\n", "compare.xrd must be a list"),
    ("report: {extra: {a: {compare: {xrd: [eds.*]}}}}\n", "extra.a: compare.xrd"),
    ("report: {sections: [compare, compare]}\n", "each at most once"),
])
def test_bad_compare_settings_are_refused_clearly(project, text, message):
    lab(project, text)
    with pytest.raises(settings_mod.SettingsError, match=re.escape(message)) as error:
        report.report_settings()
    assert "config/lab.yaml" in str(error.value)


@pytest.mark.parametrize("text", [
    "report: {compare: all}\n",
    "report: {compare: {xrd: [xrd.a.fcc, xrd.size.fcc], eds: false}}\n",
    "report: {compare: {eds: ['eds.at.*'], xrd: true}}\n",
    "report: {extra: {only-xrd: {compare: {eds: false}}}}\n",
])
def test_good_compare_settings_pass(project, text):
    lab(project, text)
    assert report.report_settings()["report"]["sections"][1] == "compare"


def test_compare_selection_names_globs_and_false():
    wanted = report.compare_wanted
    mapping = {"xrd": ["xrd.a.fcc"], "eds": ["eds.at.*"]}
    assert wanted("all", "xrd", "xrd.size.fcc") and wanted(mapping, "xrd", "xrd.a.fcc")
    assert not wanted(mapping, "xrd", "xrd.size.fcc")
    assert wanted(mapping, "eds", "eds.at.Pt") and not wanted(mapping, "eds", "eds.at_minus_nominal.Pt")
    assert not wanted({"eds": False}, "eds", "eds.at.Pt") and wanted({"eds": False}, "xrd", "xrd.a.fcc")
    assert wanted({"eds": True}, "eds", "eds.at.Pt")


def test_the_setting_decides_what_is_drawn_and_unmatched_names_are_listed(project):
    lab(project, "report: {compare: {xrd: [xrd.a.fcc, xrd.size.fcc, xrd.a.hcp], eds: false}}\n")
    result = compare.build(CONFIG)
    assert set(figures(result)) == {("xrd", "xrd.a.fcc")}
    assert result["unmatched"] == [{"method": "xrd", "name": "xrd.a.hcp",
                                    "reason": "no measurement offers it"}]
    assert [s["name"] for s in result["skipped"]] == ["xrd.size.fcc"]
    assert not (project / FOLDER / "eds").exists()                    # hidden: never drawn


@pytest.mark.parametrize("text, message", [
    ("plots: {compare: {width_in: -1}}\n", "plots.compare.width_in"),
    ("plots: {compare: {marker_cycle: [o]}}\n", "marker_cycle"),
    ("plots: {compare: {palette: red}}\n", "plots.compare.palette"),
    ("plots: {compare: {point_offset: 3}}\n", "plots.compare.point_offset"),
    ("plots: {compare: {colour: x}}\n", "colour"),
    ("plots: {compare: 3}\n", "plots.compare"),
])
def test_bad_compare_plot_style_is_refused(project, text, message):
    lab(project, text)
    with pytest.raises(settings_mod.SettingsError, match=message):
        settings_mod.plot_style()


def test_compare_style_stays_out_of_the_method_settings(project, monkeypatch):
    """No method's plot fingerprint (so no "plot out of date") depends on plots.compare."""
    shutil.copy(REPO / "config" / "xrd.yaml", project / "config" / "xrd.yaml")
    lab(project, "plots: {compare: {marker_size: 7}}\n")
    resolved, sources = settings_mod.resolve("xrd", "S-1", None)
    assert "compare" not in resolved["plots"]
    lab(project, "plots: {}\n")
    other, _ = settings_mod.resolve("xrd", "S-1", None)
    assert settings_mod.plot_fingerprint(resolved) == settings_mod.plot_fingerprint(other)


def test_the_default_style_equals_plots_yaml():
    shipped = yaml.safe_load((REPO / "config" / "plots.yaml").read_text(encoding="utf-8"))
    assert shipped["compare"] == compare.DEFAULT_STYLE


def test_figure_size_comes_from_the_compare_block(project):
    from PIL import Image
    compare.build(CONFIG)
    png = project / FOLDER / "xrd" / "xrd.a.fcc" / "plot.png"
    with Image.open(png) as image:
        assert image.size[0] == round(3.25 * 300)
        small = image.size[1]
    lab(project, "plots: {compare: {width_in: double, height_per_sample_in: 0.6}}\n")
    compare.build(CONFIG)
    with Image.open(png) as image:
        assert image.size[0] == round(7.0 * 300) and image.size[1] > small


# --- the versions stay put ----------------------------------------------------------------------

def test_compare_is_outside_every_methods_analysis_version():
    for method, info in versions.all_versions().items():
        assert "labpipe.compare" not in info["analysis_modules"], method
        assert "labpipe.compare" not in info["plot_modules"], method
    assert "labpipe.compare" in versions.compare_version()["compare_modules"]


# --- the report ----------------------------------------------------------------------------------

def build_report(project):
    result = report.build(CONFIG)
    return result, (project / "analysis" / "report.html").read_text(encoding="utf-8")


def test_report_section_per_method_with_captions_and_the_skipped_line(project):
    _, page = build_report(project)
    assert page.index("id=postdoc") < page.index("id=compare") < page.index("id=overview")
    section = page.split("<section id=compare")[1].split("</section>")[0]
    assert "<h2 class=sec>Compare samples</h2>" in section
    assert section.index('id="compare-eds"') < section.index('id="compare-xrd"')
    assert section.count("<figure>") == 3
    captions = re.findall(r"<figcaption>([^<]*)</figcaption>", section)
    assert len(captions) == 3 and all(captions)
    assert any("5 measurements of 4 samples" in c and "no σ, so no bar" in c for c in captions)
    assert section.count("<svg") == 3 and section.count("<div class=cf ") == 3
    assert re.findall(r'data-chip="([^"]*)"', section) == ["S-1", "S-2", "S-3", "S-10"]
    assert "data-chips=all" in section and 'href="_compare/xrd/xrd.a.fcc/plot.png"' in section
    assert ">PDF</a>" in section and ">provenance</a>" in section
    assert "Not drawn: xrd.size.fcc (only 1 sample)." in section
    assert (project / FOLDER / "xrd" / "xrd.a.fcc" / "plot.png").exists()


def test_report_nav_how_to_read_and_folding(project):
    _, page = build_report(project)
    nav = re.search(r"<nav class=top.*?</nav>", page, re.S).group(0)
    assert 'href="#compare"' in nav and nav.index("#postdoc") < nav.index("#compare") < nav.index("#overview")
    assert "<span class=sep>" in nav and "Methods" in nav and "Samples" in nav
    box = re.search(r"<details class=howto>.*?</details>", page, re.S).group(0)
    assert "<b>Compare samples</b>" in box and "grey diamond" in box
    assert page.count('<details class="blk cmp" open') == 2
    assert "IntersectionObserver" in page and "@media print{nav.top,.chips{display:none}" in page


def test_report_without_the_section_has_no_figures_link_or_bullet(project):
    lab(project, "report: {sections: [overview, measurements]}\n")
    _, page = build_report(project)
    assert "id=compare" not in page and 'href="#compare"' not in page
    assert "Compare samples" not in page
    assert not (project / FOLDER).exists()                       # nothing drawn either


def test_report_compare_setting_hides_a_method_per_report(project):
    lab(project, "report: {compare: {eds: false}, extra: {x: {compare: {xrd: false}}}}\n")
    report.build(CONFIG)
    main = (project / "analysis" / "report.html").read_text(encoding="utf-8")
    extra = (project / "analysis" / "report-x.html").read_text(encoding="utf-8")
    assert 'id="compare-xrd"' in main and 'id="compare-eds"' not in main
    assert 'id="compare-eds"' not in extra and 'id="compare-xrd"' not in extra
    assert (project / FOLDER / "xrd" / "xrd.a.fcc").exists()


def test_report_says_when_there_is_nothing_to_compare_and_when_drawing_failed(project, monkeypatch):
    lab(project, "report: {compare: {xrd: [xrd.size.fcc], eds: false}}\n")
    section = build_report(project)[1].split("<section id=compare")[1].split("</section>")[0]
    assert "Nothing to compare yet" in section and "xrd.size.fcc (only 1 sample)" in section
    lab(project, "report: {}\n")
    monkeypatch.setattr(compare, "render", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("no <display>")))
    page = build_report(project)[1]
    assert "could not be drawn: RuntimeError: no &lt;display&gt;" in page
    assert "Overview" in page                                    # the report still came out


# --- the command ---------------------------------------------------------------------------------

def test_cli_compare_prints_the_figures_and_what_was_kept(project, capsys):
    assert cli.cmd_compare(SimpleNamespace(method=None), CONFIG) == 0
    first = json.loads(capsys.readouterr().out)
    assert {f["status"] for f in first["figures"]} == {"drawn"} and first["dir"] == FOLDER
    assert first["skipped"] == [{"method": "xrd", "name": "xrd.size.fcc",
                                 "reason": "only 1 sample", "covered": False}]
    assert cli.cmd_compare(SimpleNamespace(method="xrd"), CONFIG) == 0
    second = json.loads(capsys.readouterr().out)
    assert [(f["name"], f["status"]) for f in second["figures"]] == [("xrd.a.fcc", "kept")]
    assert cli.cmd_compare(SimpleNamespace(method="nope"), CONFIG) == 1
    assert "unknown method" in json.loads(capsys.readouterr().out)["error"]


def test_cli_replay_takes_a_comparison_figure(project, capsys):
    compare.build(CONFIG)
    args = SimpleNamespace(provenance=str(project / FOLDER / "eds" / "eds.at.Pt" / "provenance.json"),
                           force=False)
    assert cli.cmd_replay(args, CONFIG) == 0
    assert json.loads(capsys.readouterr().out)["all_outputs_identical"] is True


# --- the methods' own figures (the `compare:` hook) ---------------------------------------------

@pytest.fixture
def hook(project, monkeypatch):
    """A fake `compare` module on the eds method: a bars figure from results.json, covering
    eds.at.*; its behaviour is set through the returned dict."""
    import sys
    import types
    from labpipe import registry, versions as versions_mod
    state = {"raise": None, "extra": [], "seen": None}
    module = types.ModuleType("fake_eds_compare")
    module.COVERS = ["eds.at.*"]

    def figures(measurements, context):
        state["seen"] = (measurements, context)
        if state["raise"]:
            raise state["raise"]
        cats = [{"name": "Pd", "color": "#D55E00"}, {"name": "Pt", "color": "#0072B2"}]
        rows = [{"sample": m["sample"], "measurement_id": m["measurement_id"],
                 "entry_id": m["entry_id"],
                 "segments": [{"category": c["name"], "value": m["results"]["metal_basis"]["at_percent"][c["name"]]}
                              for c in cats],
                 "nominal_segments": None, "results_path": m["results_path"],
                 "results_sha256": m["results_sha256"]} for m in measurements]
        return [{"kind": "bars", "name": "composition", "title": "EDS composition",
                 "unit_label": "at%", "categories": cats, "rows": rows}] + state["extra"]
    module.figures = figures
    monkeypatch.setitem(sys.modules, "fake_eds_compare", module)
    real = registry.load_methods

    def patched():
        methods = without_own_figures(real())
        methods["eds"] = methods["eds"] | {"compare": "fake_eds_compare"}
        return methods
    for mod in (compare, report, dataset, versions_mod):
        monkeypatch.setattr(mod, "load_methods", patched)
    return state


def test_a_hook_figure_is_drawn_with_all_outputs_and_the_covered_quantities_are_left_out(project, hook):
    result = compare.build(CONFIG)
    assert set(figures(result)) == {("xrd", "xrd.a.fcc"), ("eds", "composition")}   # not eds.at.*
    folder = project / FOLDER / "eds" / "composition"
    assert {p.name for p in folder.iterdir()} == {"plot.png", "plot.pdf", "figure.html", "provenance.json"}
    prov = provenance(project, "eds", "composition")
    assert prov["kind"] == "bars" and set(prov["outputs"]) == {"plot.png", "plot.pdf", "figure.html"}
    assert prov["spec"]["method"] == "eds" and prov["spec"]["plot"]["dpi"] == 300
    assert prov["outputs"]["figure.html"] == sha256_file(folder / "figure.html")
    measurements, context = hook["seen"]
    assert [m["sample"] for m in measurements] == ["S-1", "S-2", "S-3"]
    assert set(measurements[0]) == {"sample", "entry_id", "measurement_id", "status", "results",
                                    "results_path", "results_sha256", "output_dir", "input",
                                    "raw_path"}
    assert measurements[0]["raw_path"] is None and set(context) == {"plot", "config"}
    assert context["plot"]["compare"]["palette"] == "okabe-ito"


def test_covered_quantities_are_drawn_when_the_setting_names_them(project, hook):
    lab(project, "report: {compare: {eds: [composition, eds.at.Pt]}}\n")
    result = compare.build(CONFIG)
    assert {n for (m, n) in figures(result) if m == "eds"} == {"composition", "eds.at.Pt"}
    assert figures(result)[("eds", "eds.at.Pt")]["covered"] is True
    lab(project, "report: {compare: {eds: [eds.at.*]}}\n")
    assert {n for (m, n) in figures(compare.build(CONFIG)) if m == "eds"} == {"eds.at.Pd", "eds.at.Pt"}


def test_replay_of_a_hook_figure_is_identical_and_notes_a_changed_source(project, hook):
    compare.build(CONFIG)
    path = project / FOLDER / "eds" / "composition" / "provenance.json"
    result = replay.replay(path, CONFIG)
    assert result["all_outputs_identical"] and result["notes"] == []
    (project / "analysis" / "S-1" / "eds" / "S-1_e" / "results.json").write_text(
        json.dumps(_eds(40.0)), encoding="utf-8")
    again = replay.replay(path, CONFIG)
    assert again["all_outputs_identical"] and any("S-1_e/results.json has changed" in n for n in again["notes"])


def test_a_figure_is_redrawn_when_the_hook_data_change_and_kept_otherwise(project, hook):
    compare.build(CONFIG)
    assert figures(compare.build(CONFIG))[("eds", "composition")]["status"] == "kept"
    (project / "analysis" / "S-1" / "eds" / "S-1_e" / "results.json").write_text(
        json.dumps(_eds(40.0)), encoding="utf-8")
    assert figures(compare.build(CONFIG))[("eds", "composition")]["status"] == "redrawn"


def test_a_failing_hook_is_skipped_with_its_reason_and_keeps_its_folder(project, hook):
    compare.build(CONFIG)
    hook["raise"] = RuntimeError("no raw file")
    result = compare.build(CONFIG)
    assert {"method": "eds", "name": "(compare hook)", "reason": "RuntimeError: no raw file",
            "covered": False} in result["skipped"]
    assert ("xrd", "xrd.a.fcc") in figures(result) and result["moved_aside"] == []
    assert (project / FOLDER / "eds" / "composition" / "provenance.json").exists()
    assert "(compare hook)" in build_report(project)[1] and "RuntimeError: no raw file" in build_report(project)[1]


def test_bad_hook_specs_and_names_are_skipped_one_by_one(project, hook):
    hook["extra"] = [{"kind": "pie", "name": "x"}, {"kind": "dots", "name": "bad name"},
                     {"kind": "bars", "name": "composition", "categories": [], "rows": []},
                     {"kind": "lines", "name": "nan", "traces": [{"sample": "S-1", "measurement_id": "m",
                                                                 "x": [1, 2], "y": [1, float("nan")]}]}]
    result = compare.build(CONFIG)
    reasons = {s["name"]: s["reason"] for s in result["skipped"]}
    assert "unknown figure kind 'pie'" in reasons["x"] and "letters, digits" in reasons["bad name"]
    assert "another figure of this method has this name" in reasons["composition"]
    assert "not plain JSON" in reasons["nan"]
    assert ("eds", "composition") in figures(result)


def test_a_figure_that_cannot_be_drawn_is_skipped_not_fatal(project, monkeypatch):
    real = compare.render

    def broken(spec, stem):
        if spec["name"] == "eds.at.Pt":
            raise RuntimeError("boom")
        return real(spec, stem)
    monkeypatch.setattr(compare, "render", broken)
    result = compare.build(CONFIG)
    assert ("eds", "eds.at.Pd") in figures(result) and ("eds", "eds.at.Pt") not in figures(result)
    assert any(s["name"] == "eds.at.Pt" and "RuntimeError: boom" in s["reason"] for s in result["skipped"])


def test_settings_check_accepts_figure_names_of_a_method_with_a_hook(project, hook):
    lab(project, "report: {compare: {eds: [composition, patterns-anything]}}\n")
    assert report.report_settings()["report"]["compare"]["eds"] == ["composition", "patterns-anything"]
    lab(project, "report: {compare: {xrd: [composition]}}\n")
    with pytest.raises(settings_mod.SettingsError, match="'composition' is not a xrd quantity"):
        report.report_settings()
    lab(project, "report: {compare: {sem: true}}\n")
    with pytest.raises(settings_mod.SettingsError, match="names no quantities or figures"):
        report.report_settings()
    lab(project, "report: {compare: {eds: [composition, nothing-here]}}\n")
    assert [u["name"] for u in compare.build(CONFIG)["unmatched"]] == ["nothing-here"]


def test_compare_selection_covered_quantities():
    wanted = report.compare_wanted
    assert not wanted("all", "eds", "eds.at.Pt", True) and wanted("all", "eds", "composition")
    assert not wanted({"eds": True}, "eds", "eds.at.Pt", True)
    assert wanted({"eds": ["eds.at.Pt"]}, "eds", "eds.at.Pt", True)
    assert not wanted({"eds": ["composition"]}, "eds", "eds.at.Pt", True)


def test_the_compare_version_covers_the_kinds_and_the_hook_but_no_analysis_version(project, hook):
    now = versions.compare_version()
    assert {"labpipe.comparekinds", "labpipe.comparekinds.dots", "labpipe.comparekinds.lines",
            "labpipe.comparekinds.bars", "labpipe.comparekinds.images"} <= set(now["compare_modules"])
    for info in versions.all_versions().values():
        assert not any(m.startswith("labpipe.comparekinds") for m in info["analysis_modules"]
                       + info["plot_modules"])


def test_a_lab_method_may_name_a_compare_module(tmp_path):
    from labpipe import labmethods
    assert "compare" in labmethods.MODULE_KEYS
    entry = {"patterns": ["*.x"], "module": "labpipe_lab.demo.analyze", "config": "lab/methods/demo/config.yaml",
             "compare": "elsewhere.module"}
    with pytest.raises(labmethods.MethodsError, match="`compare` must name a module under"):
        labmethods._check("demo", entry, {})
    assert labmethods._check("demo", entry | {"compare": "labpipe_lab.demo.compare"}, {})["lab"]


def test_report_has_the_script_for_chips_and_the_thicker_scroll_bar(project):
    page = build_report(project)[1]
    assert "scrollbar-width:auto" in page and "nav.top::-webkit-scrollbar{height:10px}" in page
    assert "scrollbar-width:thin" not in page
    assert "data-chip" in page and "beforeprint" in page and ".chips" in page
