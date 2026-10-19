"""Plot settings: the lab.yaml layer, the plot style, plot keys left out of the analysis
fingerprint ("plot out of date"), `labpipe replot`, and the per-method plot keys."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from labpipe import settings
from labpipe.plotting import acs
from labpipe.provenance import sha256_file
from tests import test_eds, test_sem
from tests.conftest import fcc_positions, synthetic_pattern, xrdml_text
from tests.test_pipeline import REPO, _labpipe, _project

PEAKS = [(p, a, w, 0.6) for p, a, w in zip(fcc_positions(3.8898), (6000.0, 3000.0, 2200.0, 2000.0),
                                           (1.2, 1.4, 1.8, 2.1))]


# --- the lab.yaml layer and the plot style ---------------------------------------------------

def _config(tmp_path, monkeypatch, **files) -> Path:
    config = tmp_path / "config"
    (config / "measurements" / "xrd").mkdir(parents=True)
    (config / "xrd.yaml").write_text("background: {order: 4}\nphases: null\n"
                                     "plot: {width_in: 3.25, dpi: 300}\n")
    shutil.copy(REPO / "config" / "plots.yaml", config / "plots.yaml")
    for name, text in files.items():
        (config / name.replace("__", "/")).write_text(text)
    monkeypatch.setattr(settings, "CONFIG", config)
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    return config


def test_lab_layer_sits_between_the_defaults_and_the_samples(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch,
            **{"lab.yaml": "xrd: {background: {order: 3}, phases: [Pt]}\n"
                           "plots: {font_size: 8}\n",
               "samples.yaml": "samples: {S-1: {xrd: {phases: [Pd]}}}\n",
               "measurements__xrd__S-1_a.yaml": "background: {order: 2}\n"})
    resolved, sources = settings.resolve("xrd", "S-1", None)
    assert resolved["background"]["order"] == 3 and resolved["phases"] == ["Pd"]
    resolved, sources = settings.resolve("xrd", "S-1", "S-1_a")
    assert resolved["background"]["order"] == 2
    assert [s["layer"].split(" (")[0] for s in sources] == [
        "global defaults", "lab defaults", "sample S-1", "measurement S-1_a",
        "plot style defaults"]
    lab = sources[1]
    assert lab["file"] == "config/lab.yaml" and lab["layer"] == "lab defaults (config/lab.yaml)"
    assert lab["sha256"] == hashlib.sha256(
        (tmp_path / "config" / "lab.yaml").read_bytes()).hexdigest()
    assert resolved["plots"]["font_size"] == 8 and resolved["plots"]["label_size"] == 10
    # without lab.yaml nothing changes and no lab source is listed
    (tmp_path / "config" / "lab.yaml").unlink()
    resolved, sources = settings.resolve("xrd", "S-1", None)
    assert resolved["background"]["order"] == 4 and resolved["plots"]["font_size"] == 9
    assert all("lab" not in s["layer"] for s in sources)


@pytest.mark.parametrize("lab, match", [
    ("xrd: {backgruond: {order: 3}}\n", "backgruond"),
    ("plots: {fontsize: 8}\n", "fontsize"),
    ("plots: {preset: nature}\n", "preset"),
    ("plots: {formats: [png, tiff]}\n", "formats"),
    ("plots: {palette: red}\n", "palette"),
])
def test_lab_layer_refuses_unknown_keys_and_bad_values(tmp_path, monkeypatch, lab, match):
    _config(tmp_path, monkeypatch, **{"lab.yaml": lab})
    with pytest.raises(settings.SettingsError, match=match):
        settings.resolve("xrd", "S-1", None)


def test_plot_style_defaults_are_the_old_acs_constants(monkeypatch):
    style = yaml.safe_load((REPO / "config" / "plots.yaml").read_text())
    assert style == acs.DEFAULT_STYLE
    assert (style["font"], style["font_size"], style["label_size"], style["line_width"],
            style["dpi"], style["single_column_in"], style["double_column_in"]) == (
        acs.ACS["font_family"], acs.ACS["font_size"], acs.ACS["axes_labelsize"],
        acs.ACS["default_lw"], acs.ACS["dpi"], acs.ACS["fig_single_w"], acs.ACS["fig_double_w"])
    assert acs.palette(style) == acs.PHASE_COLORS
    acs.apply_acs(style)
    import matplotlib as mpl
    rc = mpl.rcParams
    assert (rc["font.size"], rc["axes.labelsize"], rc["axes.titlesize"], rc["xtick.labelsize"],
            rc["legend.fontsize"], rc["lines.linewidth"], rc["savefig.dpi"]) == (
        acs.ACS["font_size"], acs.ACS["axes_labelsize"], acs.ACS["axes_titlesize"],
        acs.ACS["tick_labelsize"], acs.ACS["legend_fontsize"], acs.ACS["default_lw"],
        acs.ACS["dpi"])
    # the shipped method files keep the same widths; the lab's global dpi reaches them
    assert yaml.safe_load((REPO / "config" / "xrd.yaml").read_text())["plot"]["width_in"] == 3.25


def test_global_dpi_is_the_default_of_the_method_plot_block(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, **{"lab.yaml": "plots: {dpi: 600}\n",
                                      "samples.yaml": "samples: {S-2: {xrd: {plot: {dpi: 150}}}}\n"})
    assert settings.resolve("xrd", "S-1", None)[0]["plot"]["dpi"] == 600
    assert settings.resolve("xrd", "S-2", None)[0]["plot"]["dpi"] == 150


def test_plot_keys_are_left_out_of_the_analysis_fingerprint(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    base = settings.resolve("xrd", None, None)[0]
    other = copy.deepcopy(base)
    other["plot"]["dpi"] = 150
    other["plots"]["font_size"] = 7
    assert settings.analysis_fingerprint(base) == settings.analysis_fingerprint(other)
    assert settings.plot_fingerprint(base) != settings.plot_fingerprint(other)
    other["background"]["order"] = 2
    assert settings.analysis_fingerprint(base) != settings.analysis_fingerprint(other)


# --- plot keys of the methods ----------------------------------------------------------------

def _xrd_resolved(**plot) -> dict:
    resolved, _ = settings.resolve("xrd", None, None)
    resolved["plot"].update(plot)
    return resolved


def _xrd_input(folder: Path, seed: int = 7) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    x, y = synthetic_pattern(PEAKS, seed=seed)
    path = folder / "S-1.xrdml"
    path.write_text(xrdml_text(x, y, "S-1"))
    return path


def test_xrd_plot_keys_never_change_results_and_zoom_adds_a_figure(tmp_path):
    from labpipe.methods.xrd import analyze
    source = _xrd_input(tmp_path / "raw")
    ctx = {"sample_id": "S-1", "measurement_id": "S-1"}

    def run(name, **plot):
        out = tmp_path / name
        analyze.run(source, out, settings=_xrd_resolved(**plot), context=ctx, pipeline=False)
        return out

    default = run("default")
    results = (default / "results.json").read_bytes()
    assert "figures" not in json.loads(results)        # default results.json is unchanged
    assert sorted(p.name for p in default.iterdir()) == ["plot.pdf", "plot.png",
                                                         "provenance.json", "results.json"]
    for name, plot in {"sqrt": {"y_scale": "sqrt"}, "log": {"y_scale": "log"},
                       "nores": {"residual_panel": False}, "noticks": {"phase_ticks": False},
                       "bare": {"residual_panel": False, "phase_ticks": False}}.items():
        out = run(name, **plot)
        assert (out / "results.json").read_bytes() == results, name
        assert (out / "plot.png").read_bytes() != (default / "plot.png").read_bytes(), name
    zoomed = run("zoom", zoom_deg=[30, 50])
    assert (zoomed / "zoom.png").exists() and (zoomed / "zoom.pdf").exists()
    data = json.loads((zoomed / "results.json").read_text())
    assert [f["file"] for f in data["figures"]] == ["plot.png", "zoom.png"]   # main plot first
    assert (zoomed / "plot.png").read_bytes() == (default / "plot.png").read_bytes()
    data.pop("figures")
    assert data == json.loads(results)
    record = json.loads((zoomed / "provenance.json").read_text())
    assert {"zoom.png", "zoom.pdf"} <= set(record["outputs"])
    assert record["parameters"]["resolved"]["plot"]["zoom_deg"] == [30, 50]
    with pytest.raises(analyze.AnalysisError, match="zoom_deg"):
        run("badzoom", zoom_deg=[50, 30])


def test_plot_style_reaches_the_figure_and_provenance_and_formats_are_listed(tmp_path):
    from labpipe.methods.sem import analyze
    image = test_sem.make_example(tmp_path / "raw")
    resolved, _ = settings.resolve("sem", None, None)
    default = analyze.run(image, tmp_path / "a", settings=copy.deepcopy(resolved),
                          context={}, pipeline=False)
    resolved["plots"].update({"font_size": 12, "formats": ["svg"]})
    styled = analyze.run(image, tmp_path / "b", settings=resolved, context={}, pipeline=False)
    assert sorted(styled["outputs"]) == ["plot.png", "plot.svg", "results.json", "thumb.jpg"]
    assert styled["outputs"]["results.json"] == default["outputs"]["results.json"]
    record = json.loads((tmp_path / "b" / "provenance.json").read_text())
    assert record["parameters"]["resolved"]["plots"]["font_size"] == 12
    assert record["plot"]["formats"] == ["png", "svg"]
    assert not (tmp_path / "b" / "plot.pdf").exists()
    assert styled["plot_sha256"] != default["plot_sha256"]
    assert styled["settings_sha256"] == default["settings_sha256"]


def test_sem_scale_bar_false_cuts_the_info_bar_and_eds_panels_choose(tmp_path):
    from PIL import Image
    from labpipe.methods.eds import analyze as eds
    from labpipe.methods.sem import analyze as sem
    image = test_sem.make_example(tmp_path / "raw")
    resolved, _ = settings.resolve("sem", None, None)
    resolved["plot"]["scale_bar"] = False
    result = sem.run(image, tmp_path / "sem", settings=resolved, context={}, pipeline=False)
    full = sem.run(image, tmp_path / "sem2", context={}, pipeline=False)
    assert result["outputs"]["results.json"] == full["outputs"]["results.json"]
    size = lambda out: Image.open(out / "plot.png").size       # noqa: E731
    assert size(tmp_path / "sem")[1] < size(tmp_path / "sem2")[1]

    report = test_eds.make_example(tmp_path / "raw")
    resolved, _ = settings.resolve("eds", None, None)
    resolved["plot"]["panels"] = ["composition", "image"]
    eds.run(report, tmp_path / "eds", settings=resolved, context={}, pipeline=False)
    figures = json.loads((tmp_path / "eds" / "results.json").read_text())["figures"]
    assert [f["file"] for f in figures] == ["panel_electron_image.jpg", "panel_composition.png"]
    assert not (tmp_path / "eds" / "panel_spectrum.jpg").exists()
    resolved["plot"]["panels"] = ["table"]
    with pytest.raises(eds.AnalysisError, match="panels"):
        eds.run(report, tmp_path / "eds2", settings=resolved, context={}, pipeline=False)


# --- plot out of date, replot, automation ----------------------------------------------------

def _analysed(tmp_path, names=("S-1",)):
    project, raw, env = _project(tmp_path)
    with open(project / "config" / "local.yaml", "a") as stream:
        stream.write("automation: {notify: {desktop: false}}\n")
    for number, name in enumerate(names):
        _xrd_input(raw / "XRD", seed=7 + number).rename(raw / "XRD" / f"{name}_test.xrdml")
    _labpipe(env, "scan")
    ids = [row["entry_id"] for row in _labpipe(env, "pending")]
    for eid in ids:
        assert _labpipe(env, "xrd", "analyze", "--entry", eid)["status"] == "done"
    return project, env, ids


def _entry(env, eid) -> dict:
    return _labpipe(env, "status", eid)


def _with_notes(project, env, eid) -> str:
    """A notes.md that names the results' hash (so no AI run is wanted for it)."""
    entry = _entry(env, eid)
    notes = f"notes on results {entry['results_sha256']}\n"
    (project / entry["output_dir"] / "notes.md").write_text(notes)
    return notes


def _set_lab(project, text):
    (project / "config" / "lab.yaml").write_text(text)


def test_a_plot_change_marks_plot_out_of_date_not_stale(tmp_path):
    project, env, (eid,) = _analysed(tmp_path)
    assert _entry(env, eid)["fingerprint_version"] == 2
    assert _labpipe(env, "scan")["plot_out_of_date"] == 0
    _set_lab(project, "plots: {font_size: 8}\nxrd: {plot: {y_scale: sqrt}}\n")
    scan = _labpipe(env, "scan")
    assert scan["changes"] == [] and scan["plot_out_of_date"] == 1
    entry = _entry(env, eid)
    assert entry["status"] == "done" and entry["plot_outdated"] is True
    assert eid in _labpipe(env, "status")["plot_out_of_date"]
    _labpipe(env, "review")
    assert "Plot out of date" in (project / "logs" / "review_queue.md").read_text()
    # an analysis setting still makes it stale
    _set_lab(project, "xrd: {background: {order: 3}}\n")
    assert _labpipe(env, "scan")["changes"][0]["to"] == "stale"


def test_replot_redraws_keeps_results_and_notes_and_replays(tmp_path):
    project, env, (eid,) = _analysed(tmp_path)
    out = project / _entry(env, eid)["output_dir"]
    results = (out / "results.json").read_bytes()
    old_png = (out / "plot.png").read_bytes()
    before = _entry(env, eid)
    notes = _with_notes(project, env, eid)
    _set_lab(project, "plots: {font_size: 8}\n")
    _labpipe(env, "scan")
    result = _labpipe(env, "replot")                 # default: the entries out of date
    assert result["replotted"] == [eid] and result["failed"] == []
    assert (out / "results.json").read_bytes() == results
    assert (out / "notes.md").read_text() == notes
    assert (out / "plot.png").read_bytes() != old_png
    aside = list((out.parent / "_superseded" / out.name).glob("*-plot"))
    assert len(aside) == 1
    assert (aside[0] / "plot.png").read_bytes() == old_png
    assert {"plot.pdf", "provenance.json"} <= {p.name for p in aside[0].iterdir()}
    after = _entry(env, eid)
    assert after["results_sha256"] == before["results_sha256"] and after["status"] == "done"
    assert after["plot_sha256"] != before["plot_sha256"] and after["plot_outdated"] is False
    assert after["settings_sha256"] == before["settings_sha256"]
    assert _labpipe(env, "scan")["plot_out_of_date"] == 0
    record = json.loads((out / "provenance.json").read_text())
    assert record["parameters"]["resolved"]["plots"]["font_size"] == 8
    assert record["outputs"]["plot.png"] == sha256_file(
        out / "plot.png")
    replayed = _labpipe(env, "replay", "--force", str(out / "provenance.json"))
    assert replayed["all_outputs_identical"], replayed
    assert _labpipe(env, "notes", "todo") == []      # the notes still describe these results
    assert _labpipe(env, "replot", "--entry", eid)["replotted"] == [eid]
    assert _labpipe(env, "replot")["replotted"] == []  # nothing out of date any more


def test_replot_refuses_to_replace_when_results_would_change(tmp_path):
    project, env, ids = _analysed(tmp_path, names=("S-1", "S-2"))
    broken = project / _entry(env, ids[0])["output_dir"]
    fine = project / _entry(env, ids[1])["output_dir"]
    tampered = (broken / "results.json").read_bytes() + b" "     # "the analysis drifted"
    (broken / "results.json").write_bytes(tampered)
    figures = {p.name: p.read_bytes() for p in broken.iterdir()}
    _set_lab(project, "plots: {font_size: 8}\n")
    _labpipe(env, "scan")
    result = _labpipe(env, "replot")
    assert result["failed"] == [ids[0]] and result["replotted"] == [ids[1]]   # moved on
    failed = next(r for r in result["results"] if r["entry_id"] == ids[0])
    assert "reproducibility problem" in failed["reason"]
    assert {p.name: p.read_bytes() for p in broken.iterdir()} == figures
    assert not (broken.parent / "_superseded").exists() or not list(
        (broken.parent / "_superseded" / broken.name).glob("*-plot"))
    kept = project / failed["kept_in"]
    assert kept.name.endswith("-replot") and (kept / "plot.png").exists()
    problem = _entry(env, ids[0])
    assert problem["status"] == "done" and problem["plot_outdated"] is True
    assert "reproducibility" in problem["replot_problem"]["reason"]
    assert ids[0] in _labpipe(env, "status")["replot_problems"]
    assert (fine / "plot.png").exists() and _entry(env, ids[1])["plot_outdated"] is False
    _labpipe(env, "review")
    assert "could not be redrawn" in (project / "logs" / "review_queue.md").read_text()


def test_replot_with_a_zoom_accepts_the_new_figures_list_only(tmp_path):
    project, env, (eid,) = _analysed(tmp_path)
    out = project / _entry(env, eid)["output_dir"]
    old = json.loads((out / "results.json").read_text())
    _with_notes(project, env, eid)
    assert _labpipe(env, "notes", "todo") == []
    _set_lab(project, "xrd: {plot: {zoom_deg: [35, 45]}}\n")
    _labpipe(env, "scan")
    assert _labpipe(env, "replot", "--method", "xrd")["replotted"] == [eid]
    new = json.loads((out / "results.json").read_text())
    assert [f["file"] for f in new.pop("figures")] == ["plot.png", "zoom.png"]
    assert new == old and (out / "zoom.png").exists()
    assert _entry(env, eid)["results_sha256"] == sha256_file(out / "results.json")
    # same analysis, another list of figures: the notes stay valid (no AI rewrite)
    assert _labpipe(env, "notes", "todo") == []


def test_automation_pass_redraws_out_of_date_plots(tmp_path):
    project, env, (eid,) = _analysed(tmp_path)
    out = project / _entry(env, eid)["output_dir"]
    old_png = (out / "plot.png").read_bytes()
    results = (out / "results.json").read_bytes()
    _with_notes(project, env, eid)
    _set_lab(project, "plots: {font_size: 8}\n")
    result = _labpipe(env, "automation", "run")
    assert result["status"] == "finished" and result["ai"] is False
    assert result["replotted"] == [eid] and result["replot_failed"] == []
    assert (out / "plot.png").read_bytes() != old_png
    assert (out / "results.json").read_bytes() == results
    assert _entry(env, eid)["plot_outdated"] is False
    assert _labpipe(env, "automation", "run")["status"] == "nothing_to_do"


def test_automation_does_not_retry_a_failed_replot_until_something_changes(tmp_path):
    project, env, (eid,) = _analysed(tmp_path)
    out = project / _entry(env, eid)["output_dir"]
    _with_notes(project, env, eid)
    (out / "results.json").write_bytes((out / "results.json").read_bytes() + b" ")
    _set_lab(project, "plots: {font_size: 8}\n")
    first = _labpipe(env, "automation", "run")
    assert first["replot_failed"] == [eid]
    again = _labpipe(env, "automation", "run")
    assert again["status"] == "nothing_to_do"        # told once, not retried every pass
    assert _labpipe(env, "replot", "--entry", eid)["failed"] == [eid]   # on request it is


def test_existing_manifests_are_converted_not_made_stale(tmp_path):
    project, env, (eid,) = _analysed(tmp_path)
    out = project / _entry(env, eid)["output_dir"]
    resolved = json.loads((out / "provenance.json").read_text())["parameters"]["resolved"]
    # what the manifest held before the split: one fingerprint over everything but `plots`,
    # with the plot block as it was then
    resolved.pop("plots")
    resolved["plot"] = {k: resolved["plot"][k] for k in ("width_in", "height_in", "dpi")}
    manifest = json.loads((project / "manifest.json").read_text())
    entry = manifest["entries"][eid]
    entry["settings_sha256"] = settings.fingerprint(resolved)
    for key in ("plot_sha256", "fingerprint_version", "plot_outdated"):
        entry.pop(key, None)
    (project / "manifest.json").write_text(json.dumps(manifest))
    scan = _labpipe(env, "scan")
    assert scan["changes"] == [] and scan["counts"] == {"done": 1}
    entry = _entry(env, eid)
    assert entry["status"] == "done" and entry["fingerprint_version"] == 2
    assert entry["plot_outdated"] is False and entry["plot_sha256"]
    # a lab plot style that exists now is not hidden by the conversion
    manifest = json.loads((project / "manifest.json").read_text())
    entry = manifest["entries"][eid]
    entry["settings_sha256"] = settings.fingerprint(resolved)
    for key in ("plot_sha256", "fingerprint_version"):
        entry.pop(key)
    (project / "manifest.json").write_text(json.dumps(manifest))
    _set_lab(project, "plots: {font_size: 8}\n")
    _labpipe(env, "scan")
    assert _entry(env, eid)["status"] == "done" and _entry(env, eid)["plot_outdated"] is True
    # an old fingerprint that does not match is stale, as before
    manifest = json.loads((project / "manifest.json").read_text())
    manifest["entries"][eid]["settings_sha256"] = "0" * 64
    manifest["entries"][eid].pop("fingerprint_version")
    (project / "manifest.json").write_text(json.dumps(manifest))
    assert _labpipe(env, "scan")["changes"][0]["to"] == "stale"
