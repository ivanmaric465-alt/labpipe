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


@pytest.fixture
def project(tmp_path, monkeypatch):
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
    return {(f["method"], f["quantity"]): f for f in result["figures"]}


# --- what is drawn -------------------------------------------------------------------------------

def test_figures_for_quantities_with_two_samples_and_skips_with_reasons(project):
    result = compare.build(CONFIG)
    drawn = figures(result)
    assert set(drawn) == {("xrd", "xrd.a.fcc"), ("eds", "eds.at.Pd"), ("eds", "eds.at.Pt")}
    assert all(f["status"] == "drawn" for f in drawn.values())
    assert {(s["quantity"], s["reason"]) for s in result["skipped"]} == {
        ("xrd.size.fcc", "only 1 sample")}
    for (method, quantity), figure in drawn.items():
        folder = project / FOLDER / method / quantity
        assert {p.name for p in folder.iterdir()} == {"plot.png", "plot.pdf", "provenance.json"}
        assert figure["png"] == f"{FOLDER}/{method}/{quantity}/plot.png"
    assert result["moved_aside"] == [] and result["unmatched"] == []


def test_samples_in_the_reports_sample_order_with_one_point_per_measurement(project):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    assert [s["sample"] for s in prov["samples"]] == ["S-1", "S-2", "S-3", "S-10"]  # natural
    assert [(p["sample"], p["measurement_id"]) for p in prov["points"]] == [
        ("S-1", "S-1_a"), ("S-2", "S-2_a"), ("S-2", "S-2_b"), ("S-3", "S-3_a"), ("S-10", "S-10_a")]
    (project / "config" / "samples.yaml").write_text(
        "samples:\n  S-10: {}\n  S-3: {}\n", encoding="utf-8")
    lab(project, "report: {sample_order: samples}\n")
    result = compare.build(CONFIG)
    assert figures(result)[("xrd", "xrd.a.fcc")]["status"] == "redrawn"      # the order is an input
    assert [s["sample"] for s in provenance(project, "xrd", "xrd.a.fcc")["samples"]] == [
        "S-10", "S-3", "S-1", "S-2"]


def test_a_sample_keeps_its_colour_and_marker_in_every_figure(project):
    compare.build(CONFIG)
    styles = [{s["sample"]: (s["color"], s["marker"]) for s in provenance(project, m, q)["samples"]}
              for m, q in (("xrd", "xrd.a.fcc"), ("eds", "eds.at.Pt"))]
    assert styles[0]["S-2"] == styles[1]["S-2"] and styles[0]["S-3"] == styles[1]["S-3"]
    assert len({v for v in styles[0].values()}) == len(styles[0])        # all different here
    assert styles[0]["S-1"][0] == "#0072B2"                                # Okabe-Ito, first


def test_no_sigma_points_and_the_caption_says_so(project):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    assert [p["sigma"] for p in prov["points"]] == [0.0035, 0.0035, 0.002, None, 0.0035]
    assert "hollow markers have no σ (S-3_a)" in prov["caption"]
    assert "Error bars: σ from the fit" in prov["caption"]
    assert "S-2 ×2" in prov["caption"] and "5 measurements of 4 samples" in prov["caption"]
    assert "No σ for any point" in provenance(project, "eds", "eds.at.Pt")["caption"]


def test_provenance_holds_the_data_the_hashes_and_the_code(project):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    assert prov["schema"] == "labpipe.compare-figure/1"
    assert (prov["method"], prov["quantity"], prov["unit"]) == ("xrd", "xrd.a.fcc", "Å")
    point = prov["points"][0]
    assert point["results_path"] == "analysis/S-1/xrd/S-1_a/results.json"
    assert point["results_sha256"] == sha256_file(project / point["results_path"])
    assert (point["entry_id"], point["key"], point["value"]) == (
        "xrd:lab:S-1_a", "phases.fcc_indexed[0].a_angstrom", 3.90)
    assert prov["plot"]["compare"]["palette"] == "okabe-ito" and prov["plot"]["dpi"] == 300
    assert prov["code"]["compare_version"] == versions.compare_version()["compare_version"]
    assert prov["code"]["labpipe_entry"] == "labpipe.compare.render" and "commit" in prov["code"]
    folder = project / FOLDER / "xrd" / "xrd.a.fcc"
    assert prov["outputs"] == {"plot.pdf": sha256_file(folder / "plot.pdf"),
                               "plot.png": sha256_file(folder / "plot.png")}
    assert prov["replay"] == f"labpipe replay {FOLDER}/xrd/xrd.a.fcc/provenance.json"
    assert prov["runtime"]["mode"] in ("native", "container")


def test_the_same_inputs_give_the_same_bytes(project, tmp_path):
    compare.build(CONFIG)
    prov = provenance(project, "xrd", "xrd.a.fcc")
    again = tmp_path / "again"
    again.mkdir()
    compare.render(prov, again / "plot")
    for name in ("plot.png", "plot.pdf"):
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
    assert provenance(project, "xrd", "xrd.a.fcc")["points"][0]["value"] == 3.95
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
    assert {(m["method"], m["quantity"]) for m in result["moved_aside"]} == {
        ("eds", "eds.at.Pd"), ("eds", "eds.at.Pt")}
    assert not (project / FOLDER / "eds" / "eds.at.Pt").exists()
    moved = project / result["moved_aside"][0]["to"]
    assert (moved / "plot.png").exists() and (moved / "provenance.json").exists()
    assert [s["quantity"] for s in result["skipped"] if s["method"] == "eds"] == [
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
    assert set(result["comparison"]) == {"plot.png", "plot.pdf"}
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
    assert result["unmatched"] == [{"method": "xrd", "quantity": "xrd.a.hcp",
                                    "reason": "no measurement offers it"}]
    assert [s["quantity"] for s in result["skipped"]] == ["xrd.size.fcc"]
    assert not (project / FOLDER / "eds").exists()                    # hidden: never drawn


@pytest.mark.parametrize("text, message", [
    ("plots: {compare: {width_in: -1}}\n", "plots.compare.width_in"),
    ("plots: {compare: {marker_cycle: []}}\n", "plots.compare.marker_cycle"),
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
    alts = re.findall(r'<figure><img alt="([^"]*)" src="[^"]*"><figcaption>([^<]*)</figcaption>',
                      section)
    assert len(alts) == 3 and all(alt == caption and caption for alt, caption in alts)
    assert any("5 measurements of 4 samples" in c and "hollow markers have no σ" in c
               for _, c in alts)
    assert "Not drawn: xrd.size.fcc (only 1 sample)." in section
    assert (project / FOLDER / "xrd" / "xrd.a.fcc" / "plot.png").exists()


def test_report_nav_how_to_read_and_folding(project):
    _, page = build_report(project)
    nav = re.search(r"<nav class=top.*?</nav>", page, re.S).group(0)
    assert 'href="#compare"' in nav and nav.index("#postdoc") < nav.index("#compare") < nav.index("#overview")
    assert "<span class=sep>" in nav and "Methods" in nav and "Samples" in nav
    box = re.search(r"<details class=howto>.*?</details>", page, re.S).group(0)
    assert "<b>Compare samples</b>" in box and "hollow marker" in box
    assert page.count('<details class="blk cmp" open') == 2
    assert "IntersectionObserver" in page and "@media print{nav.top{display:none}" in page


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
    assert first["skipped"] == [{"method": "xrd", "quantity": "xrd.size.fcc",
                                 "reason": "only 1 sample"}]
    assert cli.cmd_compare(SimpleNamespace(method="xrd"), CONFIG) == 0
    second = json.loads(capsys.readouterr().out)
    assert [(f["quantity"], f["status"]) for f in second["figures"]] == [("xrd.a.fcc", "kept")]
    assert cli.cmd_compare(SimpleNamespace(method="nope"), CONFIG) == 1
    assert "unknown method" in json.loads(capsys.readouterr().out)["error"]


def test_cli_replay_takes_a_comparison_figure(project, capsys):
    compare.build(CONFIG)
    args = SimpleNamespace(provenance=str(project / FOLDER / "eds" / "eds.at.Pt" / "provenance.json"),
                           force=False)
    assert cli.cmd_replay(args, CONFIG) == 0
    assert json.loads(capsys.readouterr().out)["all_outputs_identical"] is True
