"""Your own analyses (kind `imported`): `labpipe methods import`, the scanner's grouping, results,
quantities, checks, replay by checking, the report card, notes verify. Tmp projects, generic names."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from PIL import Image

from tests.test_pipeline import REPO, _labpipe, _project

DESCRIPTION = "Raman D/G fits made in Origin"
TABLE = "Peak,Position (cm-1),ID/IG,sd\nD,1350.2,0.60,0.02\nG,1590.7,0.60,0.02\n"
IMPORT = ("methods", "import", "raman_fits", "--folder", "Raman/fits", "--files",
          "*.png,*.csv,*.md", "--description", DESCRIPTION)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(raw: Path, sample: str = "S-1", table: str | None = TABLE, folder: str = "Raman/fits",
           stem: str | None = None) -> None:
    stem = stem or f"{sample}_raman"
    (raw / folder).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 40), (200, 30, 30)).save(raw / folder / f"{stem}.png")
    if table is not None:
        (raw / folder / f"{stem}.csv").write_text(table, encoding="utf-8", newline="")
    (raw / folder / f"{stem}.md").write_text("Two Lorentzians, Origin 2023.\n", encoding="utf-8", newline="")


def _describe(project: Path, text: str = "Raman spectra of the S-series films, two Lorentzians "
              "(D and G) fitted in Origin.") -> None:
    page = project / "lab/methods/raman_fits/README.md"
    body = page.read_text(encoding="utf-8")
    page.write_text(body.replace(body[body.index("TODO"):body.index("\n\n<!--")], text),
                    encoding="utf-8")


def _own(tmp_path, **kwargs):
    project, raw, env = _project(tmp_path)
    _files(raw, **kwargs)
    made = _labpipe(env, *IMPORT)
    return project, raw, env, made


def _analyse(env, relative: str) -> dict:
    scanned = _labpipe(env, "scan")
    entry = next(e for e in _labpipe(env, "pending") if e["relative_path"] == relative)
    done = _labpipe(env, "raman_fits", "analyze", "--entry", entry["entry_id"])
    assert done["status"] == "done", (done, scanned)
    return {**entry, **done}


def _results(project: Path, done: dict) -> dict:
    return json.loads((project / done["output_dir"] / "results.json").read_text(encoding="utf-8"))


def test_import_creates_the_files_and_never_overwrites(tmp_path):
    project, raw, env, made = _own(tmp_path)
    assert sorted(made["created"]) == sorted([
        "lab/methods/raman_fits/README.md", "lab/methods/raman_fits/config.yaml",
        "lab/agents/raman_fits-analyst.md", "lab/methods.yaml (entry)"])
    assert not (project / "lab/methods/raman_fits/analyze.py").exists()      # no Python written
    entry = yaml.safe_load((project / "lab/methods.yaml").read_text(encoding="utf-8")
                           )["methods"]["raman_fits"]
    assert entry == {"kind": "imported", "description": DESCRIPTION, "sample_from": "file",
                     "patterns": ["Raman/fits/**/*.png", "Raman/fits/**/*.csv",
                                  "Raman/fits/**/*.md"]}
    role = (project / "lab/agents/raman_fits-analyst.md").read_text(encoding="utf-8")
    assert "name: raman_fits-analyst" in role and "lab/methods/raman_fits/README.md" in role
    assert "{{" not in role and "labpipe notes verify" in role and "never" in role.lower()
    page = (project / "lab/methods/raman_fits/README.md").read_text(encoding="utf-8")
    assert "{{" not in page and "Raman/fits" in page and "TODO" in page
    assert any("labpipe scan" in step for step in made["next"])
    # the registry sees it with the shipped module, settings file and analyst filled in
    spec = _labpipe(env, "methods", "versions")["raman_fits"]
    assert "labpipe.methods.imported.analyze" in spec["analysis_modules"]
    assert spec["analysis_data"] == ["lab/methods/raman_fits/README.md"]
    # never overwrites, bad names, bad folders
    assert "already" in _labpipe(env, *IMPORT)["error"]
    before = (project / "lab/methods.yaml").read_text(encoding="utf-8")
    other = ["methods", "import", "other", "--files", "*.png", "--description", "x"]
    assert "error" in _labpipe(env, *other, "--folder", "/")
    assert "error" in _labpipe(env, *other[:2], "scan", *other[3:], "--folder", "A")
    assert "error" in _labpipe(env, *other, "--folder", "A/../B")
    assert "error" in _labpipe(env, "methods", "import", "x2", "--folder", "A", "--files",
                               "A/*.png", "--description", "x")
    (project / "lab/agents/third-analyst.md").parent.mkdir(exist_ok=True)
    (project / "lab/agents/third-analyst.md").write_text("mine", encoding="utf-8")
    clash = _labpipe(env, "methods", "import", "third", "--folder", "A", "--files", "*.png",
                     "--description", "x")
    assert "would overwrite" in clash["error"]
    assert (project / "lab/methods.yaml").read_text(encoding="utf-8") == before
    assert not (project / "lab/methods/third").exists()
    # no analyst agent: no role, no notes
    none = _labpipe(env, "methods", "import", "quiet", "--folder", "Q", "--files", "*.png",
                    "--description", "x", "--no-analyst")
    assert "lab/agents/quiet-analyst.md" not in none["created"]
    quiet = yaml.safe_load((project / "lab/methods.yaml").read_text(encoding="utf-8")
                           )["methods"]["quiet"]
    assert quiet["subagent"] is None and quiet["notes"] is False


def test_import_is_refused_in_the_repository_itself(tmp_path):
    env = {**_project(tmp_path)[2], "LABPIPE_PROJECT": str(REPO)}
    assert "project thing" in _labpipe(env, *IMPORT)["error"]
    assert not (REPO / "lab").exists()


def test_a_wrong_lab_entry_is_named(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    registry = project / "lab/methods.yaml"
    def scan() -> dict:       # a broken registry exits with 2 and says where
        done = subprocess.run([sys.executable, "-m", "labpipe.cli", "scan"], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=600)
        return json.loads(done.stdout)

    for good, bad in (("    kind: imported\n", "    kind: imported\n    module: x.y\n"),
                      ("sample_from: file", "sample_from: both")):
        registry.write_text(registry.read_text(encoding="utf-8").replace(good, bad),
                            encoding="utf-8")
        assert "raman_fits" in scan()["error"]
        registry.write_text(registry.read_text(encoding="utf-8").replace(bad, good),
                            encoding="utf-8")
    assert "counts" in scan()


def test_scan_groups_figure_table_and_text_of_one_stem(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    _files(raw, "S-2", table=TABLE.replace("1350.2", "1352.2"))     # identical files are duplicates
    before = {p: _sha(p) for p in raw.rglob("*") if p.is_file()}
    scanned = _labpipe(env, "scan")
    assert scanned["counts"] == {"duplicate": 4, "pending": 2}
    pending = _labpipe(env, "pending")
    assert [e["relative_path"] for e in pending] == [
        "Raman/fits/S-1_raman.csv", "Raman/fits/S-2_raman.csv"]      # the table is the primary
    assert [e["sample_id"] for e in pending] == ["S-1", "S-2"]
    manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"]
    companions = [e for e in manifest.values() if e["status"] == "duplicate"]
    assert {e["relative_path"] for e in companions} >= {"Raman/fits/S-1_raman.png",
                                                        "Raman/fits/S-1_raman.md"}
    assert all(e["duplicate_of"] in manifest and "main file" in e["status_reason"]
               for e in companions)
    assert _labpipe(env, "scan")["changes"] == []                  # idempotent
    assert {p: _sha(p) for p in raw.rglob("*") if p.is_file()} == before


def test_the_primary_is_the_table_then_the_image_then_the_text():
    from labpipe.methods.imported.analyze import primary_of
    assert primary_of({".png", ".csv", ".md", ".pdf"}) == ".csv"
    assert primary_of({".png", ".md", ".pdf"}) == ".png"
    assert primary_of({".md", ".pdf"}) == ".md"
    assert primary_of({".pdf", ".zzz"}) == ".pdf"
    assert primary_of({".tif", ".png"}) == ".png"


def test_the_sample_can_come_from_the_folder(tmp_path):
    project, raw, env = _project(tmp_path)
    _files(raw, folder="Raman/byfolder/S-3", stem="fit")
    _files(raw, folder="Raman/byfolder/S-4", stem="fit", table=TABLE.replace("0.60", "0.70"))
    (raw / "Raman/byfolder/loose.csv").write_text(TABLE.replace("1350.2", "1349"), encoding="utf-8")
    _labpipe(env, "methods", "import", "raman_fits", "--folder", "Raman/byfolder", "--files",
             "*.png,*.csv,*.md", "--description", DESCRIPTION, "--sample-from", "folder")
    _labpipe(env, "scan")
    rows = {e["relative_path"]: e for e in json.loads(
        (project / "manifest.json").read_text(encoding="utf-8"))["entries"].values()}
    first = rows["Raman/byfolder/S-3/fit.csv"]
    assert (first["sample_id"], first["measurement_id"], first["status"]) == (
        "S-3", "S-3_fit", "pending")
    assert rows["Raman/byfolder/S-3/fit.png"]["sample_id"] == "S-3"
    assert rows["Raman/byfolder/S-4/fit.csv"]["measurement_id"] == "S-4_fit"
    assert rows["Raman/byfolder/loose.csv"]["status"] == "needs_review"   # folder "byfolder"
    done = _labpipe(env, "raman_fits", "analyze", "--entry", first["entry_id"])
    assert done["output_dir"] == "analysis/S-3/raman_fits/S-3_fit"
    unclear = _labpipe(env, "inventory")["roots"][0]["no_sample_id"]["examples"]
    assert unclear == ["Raman/byfolder/loose.csv"]      # the folder rule is the inventory's too


def test_results_copy_the_table_verbatim_and_record_hashes_and_raw_files_stay(tmp_path):
    table = "Peak;Position (cm-1);ID/IG\nD;1350,20;0.60\nG;1590.7000;5e-1\n"
    project, raw, env, _ = _own(tmp_path, table=table)
    _describe(project)
    before = {p: (_sha(p), p.stat().st_mtime_ns) for p in raw.rglob("*") if p.is_file()}
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    results = _results(project, done)
    assert results["made_by"] == "outside labpipe" and results["kind"] == "imported"
    [shown] = results["tables"]
    assert shown["columns"] == ["Peak", "Position (cm-1)", "ID/IG"]
    assert shown["rows"] == [["D", "1350,20", "0.60"], ["G", "1590.7000", "5e-1"]]   # as written
    assert shown["n_rows"] == 2 and not shown["truncated"]
    hashes = {f["name"]: (f["sha256"], f["kind"]) for f in results["files"]}
    assert hashes == {"S-1_raman.csv": (_sha(raw / "Raman/fits/S-1_raman.csv"), "table"),
                      "S-1_raman.png": (_sha(raw / "Raman/fits/S-1_raman.png"), "image"),
                      "S-1_raman.md": (_sha(raw / "Raman/fits/S-1_raman.md"), "text")}
    assert results["texts"][0]["text"] == "Two Lorentzians, Origin 2023.\n"
    assert results["description"]["first_paragraph"].startswith("Raman spectra of the S-series")
    out = project / done["output_dir"]
    assert sorted(p.name for p in out.iterdir()) == ["plot.png", "provenance.json", "results.json"]
    with Image.open(out / "plot.png") as copy:
        assert copy.size == (60, 40)
    record = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert record["made_by"] == "outside labpipe"
    assert {s["sha256"] for s in record["sources"]} == {h for h, _ in hashes.values()}
    assert record["outputs"]["results.json"] == _sha(out / "results.json")
    assert record["code"]["analysis_version"]
    assert {p: (_sha(p), p.stat().st_mtime_ns) for p in raw.rglob("*") if p.is_file()} == before


def test_a_table_longer_than_max_rows_is_cut_and_says_so(tmp_path):
    table = "x (a.u.),y (a.u.)\n" + "".join(f"{i},{i * 2}\n" for i in range(30))
    project, raw, env, _ = _own(tmp_path, table=table)
    config = project / "lab/methods/raman_fits/config.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("max_rows: 200", "max_rows: 5"),
                      encoding="utf-8")
    results = _results(project, _analyse(env, "Raman/fits/S-1_raman.csv"))
    [shown] = results["tables"]
    assert len(shown["rows"]) == 5 and shown["n_rows"] == 30 and shown["truncated"]
    assert "table_truncated" in {c["code"] for c in results["checks"]}


def test_quantities_reach_the_dataset(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    _files(raw, "S-2", table="Peak,Position (cm-1),ID/IG,sd\nD,1351.0,0.75,0.03\n")
    config = project / "lab/methods/raman_fits/config.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace(
        "quantities: {}", 'quantities:\n  raman.id_ig:\n    column: "ID/IG"\n    unit: ""\n'
        '    sigma_column: "sd"\n  raman.d:\n    column: "Position (cm-1)"\n    unit: "cm-1"\n'
        '    row: 1'), encoding="utf-8")
    _labpipe(env, "scan")
    for entry in _labpipe(env, "pending"):
        _labpipe(env, "raman_fits", "analyze", "--entry", entry["entry_id"])
    data = _labpipe(env, "dataset")
    found = {s: {m["measurement_id"]: m["quantities"] for m in block["measurements"]}
             for s, block in data["samples"].items()}
    assert found["S-1"]["S-1_raman"]["raman.id_ig"] == {
        "value": 0.6, "sigma": 0.02, "unit": "", "key": "quantities[raman.id_ig].value",
        "label": "raman.id_ig"}
    assert found["S-2"]["S-2_raman"]["raman.id_ig"]["value"] == 0.75
    assert found["S-2"]["S-2_raman"]["raman.d"]["unit"] == "cm-1"
    assert found["S-2"]["S-2_raman"]["raman.d"]["sigma"] is None
    assert "quantities_error" not in str(data)


@pytest.mark.parametrize("table, quantities, codes, status", [
    ("Peak,Position\nD,1350\n", "", {"table_no_units", "description_missing"}, "ok"),
    (TABLE, 'q.a:\n    column: "Nope"', {"quantity_column_missing"}, "review"),
    ("Peak,Position (cm-1)\nD,n.d.\n", 'q.a:\n    column: "Position (cm-1)"',
     {"quantity_not_numeric"}, "review"),
    (TABLE, 'q.a:\n    column: "ID/IG"\n    sigma_column: "Nope"',
     {"quantity_sigma_unreadable"}, "ok"),
    (None, "", {"figure_without_table"}, "ok"),
])
def test_the_checks_fire(tmp_path, table, quantities, codes, status):
    project, raw, env, _ = _own(tmp_path, table=table)
    if quantities:
        config = project / "lab/methods/raman_fits/config.yaml"
        config.write_text(config.read_text(encoding="utf-8").replace(
            "quantities: {}", "quantities:\n  " + quantities.replace("\n", "\n  ")),
            encoding="utf-8")
    primary = "Raman/fits/S-1_raman." + ("csv" if table else "png")
    results = _results(project, _analyse(env, primary))
    assert codes <= {c["code"] for c in results["checks"]}, results["checks"]
    assert results["status"] == status


def test_description_checks(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    page = project / "lab/methods/raman_fits/README.md"
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    template = {c["code"]: c["message"] for c in _results(project, done)["checks"]}
    assert "template" in template["description_missing"]
    _describe(project)
    _labpipe(env, "scan")                              # the page is part of the analysis version
    assert _labpipe(env, "status", done["entry_id"])["status"] == "stale"
    _labpipe(env, "raman_fits", "analyze", "--entry", done["entry_id"], "--rerun")
    assert "description_missing" not in {c["code"] for c in _results(project, done)["checks"]}
    page.unlink()
    _labpipe(env, "raman_fits", "analyze", "--entry", done["entry_id"], "--rerun")
    assert "does not exist" in {c["code"]: c["message"] for c in
                                _results(project, done)["checks"]}["description_missing"]


def test_excel_is_not_read_and_a_broken_image_does_not_stop_the_rest(tmp_path):
    project, raw, env, _ = _own(tmp_path, table=None)
    (raw / "Raman/fits/S-1_raman.xlsx").write_bytes(b"PK\x03\x04 not really")
    (raw / "Raman/fits/S-1_raman.png").write_bytes(b"not a png")
    _labpipe(env, "methods", "import", "xl", "--folder", "Raman/fits", "--files", "*.xlsx",
             "--description", "x")
    registry = project / "lab/methods.yaml"
    data = yaml.safe_load(registry.read_text(encoding="utf-8"))
    data["methods"]["raman_fits"]["patterns"].append("Raman/fits/**/*.xlsx")
    del data["methods"]["xl"]
    registry.write_text(yaml.safe_dump(data), encoding="utf-8")
    results = _results(project, _analyse(env, "Raman/fits/S-1_raman.xlsx"))
    codes = {c["code"]: c for c in results["checks"]}
    assert "export" in codes["excel_not_read"]["message"] and codes["excel_not_read"]["severity"] == "review"
    assert "image_unreadable" in codes and results["status"] == "review"
    assert results["texts"]                        # the .md was still read
    assert results["tables"] == [] and {f["kind"] for f in results["files"]} >= {"table", "image"}


def test_a_companion_that_changes_analyses_the_measurement_again(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    assert _labpipe(env, "scan")["changes"] == []
    (raw / "Raman/fits/S-1_raman.md").write_text("Now with a remark.\n", encoding="utf-8")
    changes = _labpipe(env, "scan")["changes"]
    assert [c["to"] for c in changes if c["entry_id"] == done["entry_id"]] == ["pending"]
    _labpipe(env, "raman_fits", "analyze", "--entry", done["entry_id"])
    assert "Now with a remark." in _results(project, done)["texts"][0]["text"]
    registry = project / "lab/methods.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace(
        '"Raman/fits/**/*.md"]', '"Raman/fits/**/*.md", "Raman/fits/**/*.pdf"]'), encoding="utf-8")
    _labpipe(env, "scan")
    (raw / "Raman/fits/S-1_raman.pdf").write_bytes(b"%PDF-1.4 x")      # a new part
    assert [c["to"] for c in _labpipe(env, "scan")["changes"]
            if c["entry_id"] == done["entry_id"]] == ["pending"]


def test_no_image_gets_a_placeholder_and_a_pdf_is_copied_and_linked(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    (raw / "Raman/fits/S-1_raman.png").unlink()
    (raw / "Raman/fits/S-1_raman.pdf").write_bytes(b"%PDF-1.4 test")
    methods = yaml.safe_load((project / "lab/methods.yaml").read_text(encoding="utf-8"))
    methods["methods"]["raman_fits"]["patterns"].append("Raman/fits/**/*.pdf")
    (project / "lab/methods.yaml").write_text(yaml.safe_dump(methods), encoding="utf-8")
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    out = project / done["output_dir"]
    assert (out / "plot.png").stat().st_size > 0                      # "no figure: table only"
    assert _results(project, done)["figures"] == []
    assert (out / "file-S-1_raman.pdf").read_bytes() == b"%PDF-1.4 test"
    _labpipe(env, "report")
    page = (project / "analysis/report.html").read_text(encoding="utf-8")
    assert 'href="S-1/raman_fits/S-1_raman/file-S-1_raman.pdf"' in page


def test_replay_checks_and_says_it_did_not_redraw(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    provenance = project / done["output_dir"] / "provenance.json"
    ok = _labpipe(env, "replay", str(provenance))
    assert ok["all_outputs_identical"] and ok["redrawn"] is False
    assert ok["message"] == "made outside labpipe: checked, not redrawn"
    assert {s["state"] for s in ok["sources"]} == {"unchanged"}
    table = raw / "Raman/fits/S-1_raman.csv"
    table.write_text(TABLE.replace("1350.2", "1351.2"), encoding="utf-8")
    changed = _labpipe(env, "replay", str(provenance))
    assert not changed["all_outputs_identical"]
    assert "Raman/fits/S-1_raman.csv: changed" in changed["problems"]
    table.unlink()
    assert "Raman/fits/S-1_raman.csv: missing" in _labpipe(env, "replay", str(provenance))["problems"]
    copy = project / done["output_dir"] / "plot.png"
    copy.write_bytes(b"x")
    assert "plot.png: changed" in _labpipe(env, "replay", str(provenance))["problems"]


def test_the_card_has_the_badge_the_description_the_figure_and_the_table(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    _describe(project)
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    assert _labpipe(env, "report")["measurements"] == 1
    page = (project / "analysis/report.html").read_text(encoding="utf-8")
    card = page[page.index('<section class=card id="S-1_raman">'):]
    card = card[:card.index("</section>")]
    assert "your analysis, made outside labpipe" in card
    assert "Raman spectra of the S-series films" in card                  # the first paragraph
    assert "data:image/png;base64," in card                               # the figure
    assert "<th class=\"\">Position (cm-1)</th>" in card and "<td class=\"num\">1590.7</td>" in card
    assert "exactly as in your file" in card and "Two Lorentzians, Origin 2023." in card
    assert "Your files: Raman/fits/S-1_raman.csv" in card
    assert "plot.pdf" not in card and done["output_dir"] in card
    assert "your analysis, made outside labpipe" not in page[:page.index('<section class=card')]


def _notes(project: Path, done: dict, body: str) -> None:
    sha = json.loads((project / "manifest.json").read_text(encoding="utf-8"))[
        "entries"][done["entry_id"]]["results_sha256"]
    (project / done["output_dir"] / "notes.md").write_text(
        f"# S-1 · S-1_raman\n\n## Summary\n{body}\n\n---\nEntry `{done['entry_id']}` · "
        f"results.json sha256 `{sha}` · notes by raman_fits-analyst (AI)\n", encoding="utf-8")


def test_notes_verify_accepts_table_numbers_and_rejects_invented_ones(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    _describe(project)
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    assert "raman_fits" in {m["method"] for m in _labpipe(env, "notes", "todo")}   # no notes yet
    _notes(project, done, "Your table gives the D band at 1350.2 and the G band at 1590.7 "
                          "(Position, cm-1), ID/IG 0.60 (plot: two bands).")
    good = _labpipe(env, "notes", "verify", "--entry", done["entry_id"])
    assert good["ok"] and good["numbers_checked"] == 3, good
    _notes(project, done, "Your table gives the D band at 1350.2 and an ID/IG of 0.83.")
    bad = _labpipe(env, "notes", "verify", "--entry", done["entry_id"])
    assert not bad["ok"] and [u["text"] for u in bad["unmatched"]] == ["0.83"]
    assert "raman_fits" not in {m["method"] for m in _labpipe(env, "notes", "todo")}


def test_the_unrecognised_files_hints_name_the_import(tmp_path):
    project, raw, env = _project(tmp_path)
    (raw / "Raman" / "fits").mkdir(parents=True)
    (raw / "Raman/fits/S-1_raman.png").write_bytes(b"x")
    _labpipe(env, "scan")
    queue = (project / "logs" / "review_queue.md").read_text(encoding="utf-8") \
        if (project / "logs" / "review_queue.md").exists() else _labpipe(env, "review") and (
            project / "logs" / "review_queue.md").read_text(encoding="utf-8")
    assert "include my own analyses in <folder>" in queue and "labpipe methods import" in queue
    from labpipe import automation
    hint = automation._own_analyses_hint("Raman/fits/S-1_raman.png")
    assert "include my own analyses in Raman/fits" in hint and "Claude" not in hint
    assert automation._own_analyses_hint("a.png") == ""


def test_the_same_file_under_two_samples_is_two_measurements(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    _files(raw, sample="S-2")                     # byte-identical files, another sample
    _labpipe(env, "scan")
    entries = json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"]
    tables = {e["relative_path"]: e["status"] for e in entries.values()
              if e["relative_path"].endswith(".csv")}
    assert tables == {"Raman/fits/S-1_raman.csv": "pending", "Raman/fits/S-2_raman.csv": "pending"}
    # within one sample identical content is still one measurement
    (raw / "Raman/fits/copy").mkdir()
    (raw / "Raman/fits/copy/S-1_raman.csv").write_bytes((raw / "Raman/fits/S-1_raman.csv").read_bytes())
    _labpipe(env, "scan")
    entries = json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"]
    copy = next(e for e in entries.values() if e["relative_path"] == "Raman/fits/copy/S-1_raman.csv")
    assert copy["status"] == "duplicate" and "identical content" in copy["status_reason"]


def test_a_removed_companion_stays_in_the_card_and_the_card_says_so(tmp_path):
    project, raw, env, _ = _own(tmp_path)
    _describe(project)
    done = _analyse(env, "Raman/fits/S-1_raman.csv")
    (raw / "Raman/fits/S-1_raman.md").unlink()
    _labpipe(env, "scan")
    _labpipe(env, "report")
    page = (project / "analysis/report.html").read_text(encoding="utf-8")
    card = page[page.index('<section class=card id="S-1_raman">'):]
    card = card[:card.index("</section>")]
    assert "Two Lorentzians, Origin 2023." in card                        # the copy stays
    assert "removed from the data folder after this analysis: Raman/fits/S-1_raman.md" in card
    assert f"labpipe raman_fits analyze --entry {done['entry_id']} --rerun" in card
