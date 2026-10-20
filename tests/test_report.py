"""The report: layout settings (config/report.yaml, lab.yaml → report), extra reports."""

from __future__ import annotations

import json
import re
import shutil
import sys
import types
from pathlib import Path

import pytest

from labpipe import automation, manifest, postdoc, report, safety
from labpipe import settings as settings_mod

REPO = Path(__file__).resolve().parents[1]
METHODS = {"xrd": {}, "eds": {}, "sem": {"report": "gallery", "gallery": "fake_gallery"}}
CHECK = {"code": "wh_unreliable", "severity": "review", "message": "R2 low"}


def _entry(method, sample, mid, out):
    return {"entry_id": f"{method}:lab:{mid}", "method": method, "sample_id": sample,
            "measurement_id": mid, "status": "done", "output_dir": out,
            "relative_path": f"{mid}.raw"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (safety, manifest, postdoc, report, automation):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "report.yaml", tmp_path / "config" / "report.yaml")
    monkeypatch.setattr(settings_mod, "CONFIG", tmp_path / "config")
    monkeypatch.setattr(settings_mod, "DEFAULTS", tmp_path / "config")
    monkeypatch.setattr(report, "load_methods", lambda: METHODS)
    gallery = types.ModuleType("fake_gallery")
    gallery.select = lambda items, settings: items
    monkeypatch.setitem(sys.modules, "fake_gallery", gallery)
    monkeypatch.setattr(settings_mod, "resolve",
                        lambda *a: ({"gallery": {"magnifications": [100]}}, []))
    entries = {}
    layout = [("xrd", "S-10", "S-10_a"), ("xrd", "S-2", "S-2_a"), ("xrd", "S-1", "S-1_a"),
              ("eds", "S-1", "S-1_e"), ("sem", "S-1", "S-1_i1"), ("sem", "S-2", "S-2_i1")]
    for method, sample, mid in layout:
        out = f"analysis/{sample}/{method}/{mid}"
        folder = tmp_path / out
        folder.mkdir(parents=True)
        results = {"status": "ok", "checks": [CHECK] if method == "xrd" else [],
                   "acquisition": {"magnification": 100}}
        (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
        (folder / ("thumb.jpg" if method == "sem" else "plot.png")).write_bytes(b"IMG")
        if mid == "S-1_a":
            (folder / "notes.md").write_text("## Summary\nPLAINWORDS here.\n", encoding="utf-8")
        entries[f"{method}:lab:{mid}"] = _entry(method, sample, mid, out)
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"schema": "labpipe.manifest/1", "entries": entries}), encoding="utf-8")
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def config(project, monkeypatch):
    monkeypatch.setattr(report, "verify_entry", lambda config, eid: {"ok": True})
    return {"raw_roots": [], "protected_paths": [], "analysis_dir": "analysis",
            "logs_dir": "logs", "manifest": "manifest.json", "agents": {}}


def lab(project, text):
    (project / "config" / "lab.yaml").write_text(text, encoding="utf-8")


def build(project, config, name="report"):
    result = report.build(config)
    return result, (project / "analysis" / f"{name}.html").read_text(encoding="utf-8")


def ids(page):
    """Order of the cards and gallery cards on the page."""
    return re.findall(r'<section class="?card[^>]*? id="([^"]+)"', page)


def positions(page, *markers):
    return [page.index(m) for m in markers]


def test_defaults_are_todays_layout(project, config):
    result, page = build(project, config)
    assert result == {"report": "analysis/report.html", "measurements": 6,
                      "reports": ["analysis/report.html"]}
    assert list(project.glob("analysis/report-*.html")) == []
    assert re.findall(r'<h2 class=sample id="([^"]+)"', page) == [
        "sample-S-1", "sample-S-2", "sample-S-10"]
    assert ids(page) == ["S-1_a", "S-1_e", "S-1-sem", "S-2_a", "S-2-sem", "S-10_a"]
    assert page.index("<h3>XRD</h3>") < page.index("<h3>EDS</h3>") < page.index("<h3>SEM</h3>")
    order = positions(page, "id=postdoc", "<h3>XRD</h3>", 'id="sample-S-1"')
    assert order == sorted(order)
    assert "PLAINWORDS" in page and "In plain words" in page and "Check before using" in page
    assert "<title>labpipe report</title>" in page and "showing" not in page
    assert '<a href="#S-1_a">' in page and '<a href="#S-1-sem">S-1</a>' in page


def test_group_by_method(project, config):
    lab(project, "report: {group_by: method}\n")
    _, page = build(project, config)
    assert re.findall(r'<h2 class=sample id="([^"]+)"', page) == [
        "method-xrd", "method-eds", "method-sem"]
    assert ids(page) == ["S-1_a", "S-2_a", "S-10_a", "S-1_e", "S-1-sem", "S-2-sem"]
    # a sample anchor at the sample's first card, once
    assert page.count('<a id="sample-S-1"></a>') == 1
    assert page.index('<a id="sample-S-1"></a>') < page.index('id="S-1_a"')
    assert page.count('id="sample-S-2"') == 1 and page.count('id="sample-S-10"') == 1
    assert 'id="sample-S-1"' in page and 'id="S-1_e"' in page


def test_group_by_none(project, config):
    lab(project, "report: {group_by: none}\n")
    _, page = build(project, config)
    assert "<h2 class=sample" not in page
    assert ids(page) == ["S-1_e", "S-1-sem", "S-1_a", "S-2-sem", "S-2_a", "S-10_a"]   # sample, method
    for sample in ("S-1", "S-2", "S-10"):
        assert page.count(f'<a id="sample-{sample}"></a>') == 1


def test_postdoc_links_have_targets_in_every_layout(project, config, monkeypatch):
    seen = {}
    monkeypatch.setattr(report, "_postdoc_section",
                        lambda config, anchors: seen.setdefault("a", dict(anchors)) and "")
    for group in ("sample", "method", "none"):
        seen.clear()
        lab(project, f"report: {{group_by: {group}}}\n")
        _, page = build(project, config)
        assert set(seen["a"]) == {"S-1", "S-2", "S-10", "S-1_a", "S-2_a", "S-10_a", "S-1_e"}
        for target in seen["a"].values():
            assert f'id="{target}"' in page, (group, target)


def test_sections_order_and_hiding(project, config):
    lab(project, "report: {sections: [measurements, overview]}\n")
    _, page = build(project, config)
    assert "id=postdoc" not in page
    assert page.index('id="sample-S-1"') < page.index("<h3>XRD</h3>")
    lab(project, "report: {sections: [overview]}\n")
    _, page = build(project, config)
    assert "<h3>XRD</h3>" in page and "<section class=card" not in page
    assert 'href="#S-' not in page         # no cards to link to
    lab(project, "report: {sections: [postdoc, measurements]}\n")
    _, page = build(project, config)
    assert "<table" not in page and 'id="S-1_a"' in page


def test_compact_cards(project, config):
    lab(project, "report: {cards: compact}\n")
    _, page = build(project, config)
    assert ids(page) == ["S-1_a", "S-1_e", "S-1-sem", "S-2_a", "S-2-sem", "S-10_a"]
    assert "Check before using" in page and "R2 low" in page
    for dropped in ("PLAINWORDS", "In plain words", "Raw file:", "Full notes", "Settings beyond"):
        assert dropped not in page
    assert page.count("<img") == 6 and "Key results" not in page and "<dl>" in page


def test_filters(project, config):
    lab(project, 'report: {methods: [xrd, sem], samples: ["S-1*", S-2]}\n')
    _, page = build(project, config)
    assert ids(page) == ["S-1_a", "S-1-sem", "S-2_a", "S-2-sem", "S-10_a"]  # S-1* also takes S-10
    assert "<h3>EDS</h3>" not in page
    assert "5 analysed measurement(s) · showing xrd, sem; samples S-1*, S-2 ·" in page
    lab(project, 'report: {samples: ["S-1"]}\n')
    _, page = build(project, config)
    assert ids(page) == ["S-1_a", "S-1_e", "S-1-sem"]
    assert "3 analysed measurement(s) · samples S-1 ·" in page


def test_extra_reports_merge_with_the_main_settings(project, config):
    lab(project, "report:\n  cards: compact\n  samples: [S-1]\n  extra:\n"
                 "    xrd only!: {methods: [xrd], group_by: none}\n"
                 "    all-samples: {samples: all}\n")
    result, main = build(project, config)
    assert result["reports"] == ["analysis/report.html", "analysis/report-xrd-only.html",
                                 "analysis/report-all-samples.html"]
    _, xrd = build(project, config, "report-xrd-only")
    assert ids(xrd) == ["S-1_a"] and "<h2 class=sample" not in xrd     # compact and samples: S-1 inherited
    assert "PLAINWORDS" not in xrd and "<title>labpipe xrd-only</title>" in xrd
    _, wide = build(project, config, "report-all-samples")
    assert len(ids(wide)) == 6 and "<h3>EDS</h3>" in wide
    assert ids(main) == ["S-1_a", "S-1_e", "S-1-sem"]


def test_extras_in_defaults_file_and_lab_merge(project, config):
    path = project / "config" / "report.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("extra: {}", "extra: {one: {cards: compact}}"),
                    encoding="utf-8")
    lab(project, "report: {extra: {two: {group_by: method}}}\n")
    assert list(report.report_settings()) == ["report", "report-one", "report-two"]


@pytest.mark.parametrize("text, message", [
    ("report: {group_bi: sample}\n", "unknown setting 'group_bi'"),
    ("report: {group_by: foo}\n", "group_by must be one of sample, method, none, not 'foo'"),
    ("report: {cards: big}\n", "cards must be one of full, compact"),
    ("report: {sections: [overview, overview]}\n", "sections must be a list made of"),
    ("report: {sections: [tables]}\n", "sections must be a list made of"),
    ("report: {methods: xrd}\n", "methods must be 'all' or a list"),
    ("report: {methods: [xrdd]}\n", "unknown method(s) xrdd; known: eds, sem, xrd"),
    ("report: {samples: 3}\n", "samples must be 'all' or a list"),
    ("report: {extra: {a: {nope: 1}}}\n", "extra.a: unknown setting 'nope'"),
    ("report: {extra: {a: {extra: {b: {}}}}}\n", "'extra' is not allowed inside an extra report"),
    ("report: {extra: {a: {group_by: foo}}}\n", "extra.a: group_by must be one of"),
    ("report: {extra: {'a b': {}, 'a-b': {}}}\n", "repeats another one"),
    ("report: {extra: {'!!': {}}}\n", "is empty or repeats"),
    ("report: [1]\n", "report: must be a mapping"),
])
def test_bad_settings_are_refused_clearly(project, config, text, message):
    lab(project, text)
    with pytest.raises(settings_mod.SettingsError) as error:
        report.build(config)
    assert message in str(error.value) and "config/lab.yaml" in str(error.value)
    assert not (project / "analysis" / "report.html").exists()      # nothing half written


def test_building_changes_nothing_else(project, config):
    lab(project, "report: {extra: {x: {methods: [xrd]}}}\n")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    report.build(config)
    after = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert {p for p in after if p not in before} == {project / "analysis" / "report.html",
                                                     project / "analysis" / "report-x.html"}
    assert all(after[p] == before[p] for p in before)


def _cards(page):
    """The page without the how-to-read box (it explains these phrases, too)."""
    return re.sub(r"<details class=howto>.*?</details>", "", page, flags=re.S)


def test_notes_that_passed_under_older_rules_are_shown_as_passed_with_a_line(
        project, config, monkeypatch):
    monkeypatch.setattr(report, "verify_entry", lambda config, eid: {
        "ok": True, "checked_with": {"rules": 1, "labpipe": "v0.3.0"}})
    page = _cards(build(project, config)[1])
    assert "PLAINWORDS" in page and "did not pass" not in page
    assert page.count("checked with the rules of labpipe v0.3.0") == 1      # the one with notes
    monkeypatch.setattr(report, "verify_entry", lambda config, eid: {"ok": False})
    assert "did not pass" in _cards(build(project, config)[1])
    monkeypatch.setattr(report, "verify_entry", lambda config, eid: {"ok": True})
    assert "checked with the rules" not in _cards(build(project, config)[1])


def _howto(page):
    match = re.search(r"<details class=howto>.*?</details>", page, re.S)
    return match.group(0) if match else None


def test_how_to_read_box_is_collapsed_by_default_and_says_what_this_report_shows(project, config):
    _, page = build(project, config)
    box = _howto(page)
    assert box and "<details class=howto>" in box and "open" not in box.split(">")[0]
    assert page.index("generated") < page.index("class=howto") < page.index("id=postdoc")
    for text in ("grouped by sample", "Overview tables", "N to check", "Check before using",
                 "In plain words", "The postdoc", "results.json", "labpipe replay",
                 "gallery of images", "how_to_read: false"):
        assert text in box
    assert "stale</span>" not in box and "waiting" not in box      # none here
    assert "checked with the rules of labpipe X" in box            # full cards


def test_how_to_read_follows_group_by_sections_and_cards(project, config):
    lab(project, "report: {group_by: method, sections: [measurements], cards: compact}\n")
    box = _howto(build(project, config)[1])
    assert "grouped by method" in box and "grouped by sample" not in box
    assert "Overview tables" not in box and "The postdoc" not in box and "In plain words" not in box
    assert "notes are left out" in box
    lab(project, "report: {group_by: none, sections: [overview]}\n")
    box = _howto(build(project, config)[1])
    assert "Overview tables" in box and "<b>Cards</b>" not in box and "<b>Check before using</b>" not in box


def test_how_to_read_mentions_stale_and_the_waiting_line_only_when_present(project, config):
    data = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    data["entries"]["xrd:lab:S-1_a"]["status"] = "stale"
    data["entries"]["x:lab:w"] = {"entry_id": "x:lab:w", "method": "xrd", "status": "failed"}
    (project / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    box = _howto(build(project, config)[1])
    assert "<span class=flag>stale</span>" in box and "logs/review_queue.md" in box


def test_how_to_read_can_be_switched_off_also_per_extra_report(project, config):
    lab(project, "report: {how_to_read: false, extra: {again: {how_to_read: true}}}\n")
    _, page = build(project, config)
    assert _howto(page) is None and "How to read this report" not in page
    assert _howto(build(project, config, "report-again")[1])
    lab(project, "report: {how_to_read: maybe}\n")
    with pytest.raises(settings_mod.SettingsError, match="how_to_read must be true or false"):
        report.build(config)


# --- sample order, tables, look --------------------------------------------------------------

def samples_yaml(project, text):
    (project / "config" / "samples.yaml").write_text(text, encoding="utf-8")


def sample_ids(page):
    return re.findall(r'<h2 class=sample id="sample-([^"]+)"', page)


def test_sample_order_name_is_natural_and_the_default(project, config):
    assert report.sample_sorter("name")("S-2") < report.sample_sorter("name")("S-10")
    assert sample_ids(build(project, config)[1]) == ["S-1", "S-2", "S-10"]
    lab(project, "report: {sample_order: name}\n")
    assert sample_ids(build(project, config)[1]) == ["S-1", "S-2", "S-10"]


def test_sample_order_samples_follows_samples_yaml_then_the_rest_by_name(project, config):
    samples_yaml(project, "samples:\n  S-10: {}\n  S-1: {}\n")
    lab(project, "report: {sample_order: samples}\n")
    _, page = build(project, config)
    assert sample_ids(page) == ["S-10", "S-1", "S-2"]
    # the same order in the nav bar, the overview rows, and the galleries
    assert re.findall(r'<a href="#sample-([^"]+)">', page.split("</nav>")[0]) == ["S-10", "S-1", "S-2"]
    assert ids(page) == ["S-10_a", "S-1_a", "S-1_e", "S-1-sem", "S-2_a", "S-2-sem"]
    xrd = page.split("<h3>XRD</h3>")[1].split("</section>")[0]
    assert xrd.index("S-10_a") < xrd.index("S-1_a") < xrd.index("S-2_a")


def test_sample_order_by_property_missing_last_by_name(project, config):
    samples_yaml(project, "samples:\n  S-1: {properties: {loading_wt: 40}}\n"
                          "  S-10: {properties: {loading_wt: 5.5}}\n")
    lab(project, "report: {sample_order: 'property:loading_wt'}\n")
    assert sample_ids(build(project, config)[1]) == ["S-10", "S-1", "S-2"]
    lab(project, "report: {group_by: method, sample_order: 'property:loading_wt'}\n")
    assert ids(build(project, config)[1]) == ["S-10_a", "S-1_a", "S-2_a", "S-1_e", "S-1-sem", "S-2-sem"]
    lab(project, "report: {group_by: none, sample_order: 'property:loading_wt'}\n")
    assert ids(build(project, config)[1])[:2] == ["S-10_a", "S-1_e"]
    lab(project, "report: {sample_order: 'property:nothing'}\n")        # nobody has it: by name
    assert sample_ids(build(project, config)[1]) == ["S-1", "S-2", "S-10"]


def test_sample_order_per_extra_report(project, config):
    samples_yaml(project, "samples:\n  S-10: {}\n")
    lab(project, "report: {extra: {rev: {sample_order: samples}}}\n")
    assert sample_ids(build(project, config)[1]) == ["S-1", "S-2", "S-10"]
    assert sample_ids(build(project, config, "report-rev")[1]) == ["S-10", "S-1", "S-2"]


@pytest.mark.parametrize("value", ["'Name'", "samples_yaml", "'property:'", "'property: x'", "3", "[name]"])
def test_bad_sample_order_is_refused(project, config, value):
    lab(project, f"report: {{sample_order: {value}}}\n")
    with pytest.raises(settings_mod.SettingsError, match="sample_order must be name, samples or property:<name>"):
        report.build(config)
    lab(project, f"report: {{extra: {{a: {{sample_order: {value}}}}}}}\n")
    with pytest.raises(settings_mod.SettingsError, match="extra.a: sample_order must be"):
        report.build(config)


EDS_ELEMENTS = {"Ti": 50.0, "Pt": 25.0, "Ru": 0.4, "O": 20.0, "C": 4.6}


def eds_results(wt, at, *, zero=None, nominal=None, low=(), non_metals=("O", "C")):
    results = {"status": "ok", "checks": [], "figures": [],
               "composition": {"elements": [{"element": e, "weight_percent": v} for e, v in wt.items()]},
               "metal_basis": {"excluded_non_metals": list(non_metals), "at_percent": at,
                               "computed_from": "weight_percent / standard atomic weight",
                               "taken_as_zero": zero or {}}}
    if nominal:
        results["nominal_comparison"] = {"rows": [
            {"element": e, "measured_at_percent": at.get(e), "nominal_at_percent": v,
             "difference_at_percent": None, "ratio": None} for e, v in nominal.items()]}
    if low:
        results["checks"].append({"code": "low_concentration", "severity": "warning",
                                  "message": "near the detection limit (< 1.0 wt%): "
                                  + ", ".join(f"{e} {wt[e]} wt%" for e in low)})
    if zero:
        results["checks"].append({"code": "negative_value", "severity": "review",
                                  "message": "INCA reported negative values"})
    return results


def xrd_results(phases, rwp=0.081):
    return {"status": "ok", "checks": [], "fit": {"global": {"rwp": rwp, "chi2_reduced": 1.2}},
            "internal_standard": None, "broad_components": [], "peaks": [], "steering": [],
            "phases": {"fcc_indexed": phases, "reference_candidates": [], "unassigned_peaks": []},
            "size_strain": [{"phase": "p1", "scherrer": [{"hkl": [1, 1, 1], "size_nm": 8.46,
                                                           "size_sigma_nm_from_fwhm": 0.31}]}]}


def phase(a=3.8898, sigma=0.0012, ref="Pd", delta=0.12, n=4, tentative=False, pid="p1"):
    return {"id": pid, "a_angstrom": a, "a_sigma_angstrom": sigma, "n_reflections": n,
            "tentative": tentative, "reflections": [{"hkl": [1, 1, 1]}],
            "reference_comparison": [{"phase": ref, "delta_a_percent": delta}] if ref else []}


@pytest.fixture
def rich(project, monkeypatch):
    """EDS rows (nominal; negative Ru and a low Pd; one old result), an XRD row, SEM."""
    monkeypatch.setattr(report, "load_methods", lambda: {
        **METHODS, "eds": {"summary": "labpipe.methods.eds.summary"},
        "xrd": {"summary": "labpipe.methods.xrd.summary"}})
    manifest_path = project / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    def put(method, sample, mid, results):
        out = f"analysis/{sample}/{method}/{mid}"
        (project / out).mkdir(parents=True, exist_ok=True)
        (project / out / "results.json").write_text(json.dumps(results), encoding="utf-8")
        (project / out / "plot.png").write_bytes(b"IMG")
        data["entries"][f"{method}:lab:{mid}"] = _entry(method, sample, mid, out)

    put("eds", "S-1", "S-1_e", eds_results(
        {"Ti": 50.0, "Pt": 25.0, "Ru": 0.4, "O": 20.0, "C": 4.6},
        {"Ti": 66.0, "Pt": 33.0, "Ru": 1.0}, low=["Ru"], nominal={"Ti": 60.0, "Pt": 30.0, "Ru": 10.0}))
    put("eds", "S-2", "S-2_e", eds_results(
        {"Ti": 60.0, "Ru": -0.3, "Pd": 10.0, "O": 29.7},
        {"Ti": 90.0, "Ru": 0.0, "Pd": 10.0}, zero={"Ru": -0.3}, non_metals=("O",)))
    old = eds_results({"Ti": 100.0}, {"Ti": 100.0}, non_metals=())
    del old["metal_basis"]["taken_as_zero"]
    put("eds", "S-10", "S-10_e", old)
    put("xrd", "S-1", "S-1_a", xrd_results([phase()]))
    put("xrd", "S-10", "S-10_a", xrd_results([phase(a=3.92, ref="Pt", delta=0.0)]))
    put("xrd", "S-2", "S-2_a", xrd_results([phase(sigma=None, ref=None, tentative=True),
                                           phase(a=3.61, pid="p2", n=3)], rwp=0.1))
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    return project


def table_rows(page, title_start):
    """Rows of the table under the <h4> that starts with `title_start`: [[cell text, ...]]."""
    block = page.split(f"<h4>{title_start}")[1].split("</table>")[0]
    return [[re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>.*?</td>", row)]
            for row in re.findall(r"<tr[^>]*>.*?</tr>", block.split("<tbody>")[1])]


def headers(page, title_start):
    block = page.split(f"<h4>{title_start}")[1].split("</thead>")[0]
    return re.findall(r"<th[^>]*>(.*?)</th>", block)


def test_eds_metals_table_has_fixed_columns_by_atomic_number(rich, config):
    _, page = build(rich, config)
    cols = [re.sub(r"<[^>]+>", " ", h).split()[0] for h in headers(page, "Metals, at%")]
    assert cols == ["Measurement", "Ti", "Ru", "Pd", "Pt", "Review"]       # Z 22, 44, 46, 78
    wt = [re.sub(r"<[^>]+>", " ", h).split()[0] for h in headers(page, "All elements")]
    assert wt == ["Measurement", "Ti", "Ru", "Pd", "Pt", "C", "O", "Review"]   # metals, then C, O
    assert "<span class=u>at%</span>" in page and "<span class=u>wt%</span>" in page


def test_eds_cells_states_bars_and_nominal_row(rich, config):
    _, page = build(rich, config)
    rows = table_rows(page, "Metals, at%")
    assert rows[0][:5] == ["S-1_e", "66.0", "1.0", "–", "33.0"]
    assert rows[1][:5] == ["nominal", "60.0", "10.0", "–", "30.0"]                  # nominal right under it
    assert rows[2][1:5] == ["90.0", "n.d.", "10.0", "–"]
    assert rows[3][1:5] == ["100.0", "–", "–", "–"]                           # old results still render
    assert 'class="nominal"' in page and "<td>nominal</td>" in page
    assert '<td class="num r nd" title="recorded −0.3 wt%, taken as 0 (review check)">n.d.</td>' in page
    assert '<td class="num r low bar" ' in page and "background-image:linear-gradient(90deg,var(--bar) 66.0%" in page
    assert '<td class="num r missing">–</td>' in page
    wt = table_rows(page, "All elements")
    assert wt[0][1:7] == ["50.0", "0.4", "–", "25.0", "4.6", "20.0"]
    assert wt[1][1:7] == ["60.0", "n.d.", "10.0", "–", "–", "29.7"]
    # wt% bar: value / column maximum (Ti: 100 in the old result)
    assert "var(--bar) 50.0%,transparent 50.0%" in page
    assert "S-10_e" in page


def test_eds_overview_keeps_review_and_links(rich, config):
    _, page = build(rich, config)
    block = page.split("<h4>Metals, at%")[1].split("</table>")[0]
    assert 'href="#S-1_e"' in block and block.count('<span class="flag o">ok</span>') == 2 and "1 to check" in block
    assert "<th>Review</th>" in block and "<th>Measurement</th>" in block


def test_results_a_method_cannot_tabulate_go_in_a_plain_table(project, config, monkeypatch):
    summary = types.ModuleType("fake_summary")
    summary.summarize = lambda r: {"overview": {"Status": r["status"]}}

    def overview_tables(items):
        return [{"columns": [{"label": "v", "align": "r"}], "rows": [
            {"item": i, "cells": [str(item["results"]["v"])]} for i, item in enumerate(items)]}]
    summary.overview_tables = overview_tables
    monkeypatch.setitem(sys.modules, "fake_summary", summary)
    monkeypatch.setattr(report, "load_methods", lambda: {**METHODS, "eds": {"summary": "fake_summary"}})
    (project / "analysis/S-1/eds/S-1_e/results.json").write_text(
        json.dumps({"status": "ok", "v": 7}), encoding="utf-8")
    _, page = build(project, config)
    eds = page.split("<h3>EDS</h3>")[1].split("</section>")[0]
    assert "<td class=\"num r\">7</td>" in eds and 'href="#S-1_e"' in eds
    lab(project, "report: {sections: [overview, measurements]}\n")
    (project / "analysis/S-1/eds/S-1_e/results.json").write_text(
        json.dumps({"status": "ok"}), encoding="utf-8")       # no "v": the plain table instead
    _, page = build(project, config)
    eds = page.split("<h3>EDS</h3>")[1].split("</section>")[0]
    assert "<th>Status</th>" in eds and 'href="#S-1_e"' in eds and "<td class=\"num r\">" not in eds


def test_xrd_table_value_and_sigma_cells(rich, config):
    _, page = build(rich, config)
    block = page.split("<h3>XRD</h3>")[1].split("</section>")[0]
    cols = [re.sub(r"<[^>]+>", " ", h).split() for h in re.findall(r"<th[^>]*>(.*?)</th>", block)]
    assert [c[0] for c in cols][:3] == ["Measurement", "Phase", "a"] and ["Å"] in [c[1:] for c in cols]
    rows = [[re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>.*?</td>", r)]
            for r in re.findall(r"<tr[^>]*>.*?</tr>", block.split("<tbody>")[1])]
    by_id = {r[0]: r for r in rows if r[0]}
    # Measurement, Phase, a, ± σ, closest ref, Δa, size, ± σ, reflections, Rwp, Review
    assert by_id["S-1_a"][1:10] == ["p1", "3.8898", "± 0.0012", "Pd", "+0.12", "8.46", "± 0.31", "4", "8.1"]
    tentative = by_id["S-2_a"]
    assert tentative[2:5] == ["3.89", "no σ", "–"] and tentative[8] == "4 (tent.)"
    second = rows[rows.index(tentative) + 1]
    assert second[0] == "" and second[1] == "p2" and second[2] == "3.6100" and second[-1] == ""
    assert "lower bound" in block and 'class="sub"' in block


def test_cards_show_key_results_as_small_tables_and_fall_back(rich, config):
    _, page = build(rich, config)
    card = _cards(page).split('id="S-1_e"')[1].split("</section>")[0]
    assert "<h3>Key results</h3><div class=\"tw\"><table>" in card and "<dl>" not in card
    assert "Metal basis" in card and "As recorded" in card and "Measured − nominal" in card
    assert "−" in card and "nominal" in card
    assert '<td class="num r nominal">60.0</td>' in card
    xrd = _cards(page).split('id="S-1_a"')[1].split("</section>")[0]
    assert "lattice parameter a (Å)" in xrd and "± 0.0012" in xrd and "Rwp (%)" in xrd
    # a method without table(): today's lines
    sem = _cards(page).split('id="S-1-sem"')[1].split("</section>")[0]
    assert "<table" not in sem
    # old results (no taken_as_zero) still get their card table
    old = _cards(page).split('id="S-10_e"')[1].split("</section>")[0]
    assert "<table" in old
    lab(project := rich, "report: {cards: compact}\n")
    compact = build(rich, config)[1].split('id="S-1_e"')[1].split("</section>")[0]
    assert "<table" in compact and "Check before using" not in compact


def test_card_hook_failures_fall_back_to_the_lines(project, config, monkeypatch):
    summary = types.ModuleType("fake_summary")
    summary.summarize = lambda r: {"overview": {"Status": r["status"]}}
    summary.table = lambda r: r["missing"]
    monkeypatch.setitem(sys.modules, "fake_summary", summary)
    monkeypatch.setattr(report, "load_methods", lambda: {**METHODS, "eds": {"summary": "fake_summary"}})
    _, page = build(project, config)
    card = page.split('id="S-1_e"')[1].split("</section>")[0]
    assert "<dt>Status</dt><dd>ok</dd>" in card


def test_nav_bar_links_only_sections_that_are_on(rich, config):
    _, page = build(rich, config)
    nav = re.search(r"<nav class=top.*?</nav>", page, re.S).group(0)
    assert nav.count("<a ") == 1 + 1 + 3 + 3          # postdoc, overview, 3 method tables, 3 samples
    for target in ("#postdoc", "#overview", "#table-xrd", "#table-eds", "#table-sem",
                   "#sample-S-1", "#sample-S-2", "#sample-S-10"):
        assert f'href="{target}"' in nav and f'id="{target[1:]}"' in page.replace("id=" + target[1:], f'id="{target[1:]}"')
    assert "Fold all" in nav and "Unfold all" in nav
    lab(rich, "report: {sections: [measurements]}\n")
    nav = re.search(r"<nav class=top.*?</nav>", build(rich, config)[1], re.S).group(0)
    assert "#postdoc" not in nav and "#overview" not in nav and "#table-" not in nav
    assert "#sample-S-1" in nav
    lab(rich, "report: {sections: [overview]}\n")
    nav = re.search(r"<nav class=top.*?</nav>", build(rich, config)[1], re.S).group(0)
    assert "#sample-" not in nav and "Fold all" not in nav and "#table-xrd" in nav
    lab(rich, "report: {sections: [postdoc], group_by: none}\n")
    assert 'href="#postdoc"' in build(rich, config)[1]
    lab(rich, "report: {sections: []}\n")
    assert "<nav" not in build(rich, config)[1]


def test_samples_fold_with_a_fold_all_control(rich, config):
    _, page = build(rich, config)
    assert page.count("<details class=blk open>") == 3
    assert '<summary><h2 class=sample id="sample-S-1">S-1</h2></summary>' in page
    assert "data-fold=close" in page and "data-fold=open" in page and "details.blk" in page
    lab(rich, "report: {group_by: method}\n")
    page = build(rich, config)[1]
    assert page.count("<details class=blk open>") == 3 and 'id="method-xrd"' in page
    lab(rich, "report: {group_by: none}\n")
    page = build(rich, config)[1]
    assert "<details class=blk" not in page and "data-fold" not in page.split("</nav>")[0]


def test_print_style_and_sticky_headers(rich, config):
    _, page = build(rich, config)
    css = page.split("<style>")[1].split("</style>")[0]
    print_css = css.split("@media print{")[-1]
    assert "nav.top{display:none}" in print_css and "break-after:page" in print_css
    assert "position:sticky" in css and ".tw.st th{position:sticky" in css
    assert "beforeprint" in page and 'class="tw st"' in page


def test_captions_are_under_the_figure_and_its_alt_text(rich, config):
    _, page = build(rich, config)
    figures = re.findall(r'<figure><img alt="([^"]*)" src="[^"]*"><figcaption>([^<]*)</figcaption>', page)
    assert len(figures) >= 8
    assert all(alt == caption and caption for alt, caption in figures)
    texts = [c for _, c in figures]
    assert any(t.startswith("S-1_e: electron image") and "no error bars" in t for t in texts)
    assert any(t.startswith("S-1_a: measured data") and "σ of the least-squares fit" in t for t in texts)
    assert any("×100" in t for t in texts)                      # the gallery's own caption


def test_how_to_read_mentions_table_marks_and_navigation_only_when_present(rich, project, config):
    box = _howto(build(rich, config)[1])
    for text in ("Marks in the tables", "n.d.", "grey numbers", "nominal", "faint bar", "Navigation",
                 "Fold all", "one card per page"):
        assert text in box
    lab(rich, "report: {sections: [measurements]}\n")
    box = _howto(build(rich, config)[1])
    assert "Marks in the tables" not in box or "n.d." in box      # card tables still show marks
    lab(rich, "report: {sections: [], how_to_read: true}\n")
    box = _howto(build(rich, config)[1])
    assert "Navigation" not in box and "Marks in the tables" not in box


def test_plain_project_has_no_table_marks_in_how_to_read(project, config):
    box = _howto(build(project, config)[1])
    assert "Marks in the tables" not in box and "Navigation" in box


def test_eds_elements_by_atomic_number_unknown_last_by_symbol():
    from labpipe.methods.eds import summary
    assert summary._by_atomic_number(["Pt", "Zz", "Ti", "Aa", "O", "C", "Ru"]) == [
        "C", "O", "Ti", "Ru", "Pt", "Aa", "Zz"]
