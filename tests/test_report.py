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
    assert 'href="#' not in page          # no cards to link to
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
