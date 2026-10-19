"""The context folder and the postdoc: init/index, run files, verify, freshness, guard, setup."""

from __future__ import annotations

import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

from labpipe import agents, context, guard, manifest, postdoc, safety, wizard
from labpipe.safety import PROJECT_ROOT

QUESTION = "How does the lattice parameter of S-9 compare with the reference?"
RESULTS = {"phases": {"a_angstrom": 3.88221, "a_sigma": 0.00297},
           "checks": [{"code": "wh_unreliable", "severity": "review", "message": "R2 low"}]}
SHA = "a" * 64


def _dataset(config):
    return {"schema": "labpipe.dataset/1", "generated_utc": "2026-10-19T10:00:00Z", "samples": {
        "S-9": {"properties": {}, "measurements": [
            {"entry_id": "xrd:lab:S-9_1.2.26.xrdml", "method": "xrd",
             "measurement_id": "S-9_1.2.26", "status": "done",
             "output_dir": "analysis/S-9/xrd/S-9_1.2.26", "results_sha256": SHA, "notes": None,
             "plot": None, "review_codes": ["wh_unreliable"],
             "quantities": {"xrd.a.fcc": {"value": 3.8822, "sigma": 0.003, "unit": "Å",
                                         "key": "phases.a_angstrom"}}}]}}}


def _entry(eid, sample, out):
    return {"entry_id": eid, "method": "xrd", "sample_id": sample, "status": "done",
            "output_dir": out, "relative_path": eid.split(":")[-1]}


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (safety, manifest, postdoc, context):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    stub = types.ModuleType("labpipe.dataset")
    stub.build = _dataset
    monkeypatch.setitem(sys.modules, "labpipe.dataset", stub)
    out = tmp_path / "analysis" / "S-9" / "xrd" / "S-9_1.2.26"
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps(RESULTS), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": "labpipe.manifest/1", "entries": {
            "xrd:lab:S-9_1.2.26.xrdml": _entry("xrd:lab:S-9_1.2.26.xrdml", "S-9",
                                               "analysis/S-9/xrd/S-9_1.2.26")}}), encoding="utf-8")
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def config(project):
    return {"raw_roots": [{"id": "lab", "path": str(project / "raw")}], "protected_paths": [],
            "analysis_dir": "analysis", "logs_dir": "logs", "manifest": "manifest.json",
            "agents": {"harness": "claude-code", "models": {"postdoc": "opus"}}}


def _docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


# ---- context -------------------------------------------------------------------------

def test_context_init_creates_and_never_overwrites(project, config):
    first = context.init(config)
    assert {"context", "context/documents", "context/context.md",
            "context/instructions.md"} <= set(first["created"])
    (project / "context" / "context.md").write_text("mine", encoding="utf-8")
    second = context.init(config)
    assert second["created"] == [] and "context/context.md" in second["kept"]
    assert (project / "context" / "context.md").read_text(encoding="utf-8") == "mine"
    assert "AGENTS.md" in (project / "context" / "instructions.md").read_text(encoding="utf-8")


def test_context_index_text_formats_and_skips_unchanged(project, config):
    context.init(config)
    docs = project / "context" / "documents"
    (docs / "plan.md").write_text("Aim: 5 samples\n", encoding="utf-8")
    (docs / "sub").mkdir()
    (docs / "sub" / "notes.txt").write_text("hello 7.5", encoding="utf-8")
    _docx(docs / "report.docx", ["First paragraph", "Pt 12.5 at%"])
    (docs / "image.png").write_bytes(b"\x89PNG")
    first = context.index(config)
    assert first["indexed"] == ["plan.md", "report.docx", "sub/notes.txt"]
    assert "image.png" in first["not_indexed"]
    text = project / "context" / ".text"
    assert (text / "report.docx.txt").read_text(encoding="utf-8") == "First paragraph\nPt 12.5 at%\n"
    index = json.loads((text / "index.json").read_text(encoding="utf-8"))["documents"]
    assert len(index["plan.md"]["sha256"]) == 64 and index["image.png"]["status"] == "not indexed"
    assert context.index(config)["unchanged"] == ["plan.md", "report.docx", "sub/notes.txt"]
    (docs / "plan.md").write_text("Aim: 6 samples\n", encoding="utf-8")
    assert context.index(config)["indexed"] == ["plan.md"]


def test_context_index_pdf_pages_and_missing_pdftotext(project, config, monkeypatch):
    context.init(config)
    pdf = project / "context" / "documents" / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(context.shutil, "which", lambda name: None)
    result = context.index(config)
    assert "pdftotext" in result["not_indexed"]["paper.pdf"]
    monkeypatch.setattr(context.shutil, "which", lambda name: "/usr/bin/pdftotext")
    monkeypatch.setattr(context.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout=b"one 4.2\n\fa = 3.89\n\f", stderr=b""))
    result = context.index(config)
    assert result["indexed"] == ["paper.pdf"] and not result["not_indexed"]
    text = (project / "context" / ".text" / "paper.pdf.txt").read_text(encoding="utf-8")
    assert text == "=== page 1 ===\none 4.2\n\n=== page 2 ===\na = 3.89\n\n"
    doc = json.loads((project / "context" / ".text" / "index.json").read_text(
        encoding="utf-8"))["documents"]["paper.pdf"]
    assert doc["pages"] == 2 and doc["status"] == "indexed"


# ---- run files -----------------------------------------------------------------------

def _run(config, project, body: str | None = None, question: str = QUESTION) -> str:
    info = postdoc.start(config, question)
    if body is not None:
        (project / info["path"] / "analysis.md").write_text(body, encoding="utf-8")
    return info["run"]


def _text(extra: str = "", question: str = QUESTION, codes: str = " `wh_unreliable`",
          cite: str = "S-9_1.2.26") -> str:
    return (f"# Analysis\n\n## Question\n{question}\n\n## Summary\n{extra}\n\n"
            f"## Check before using\n- {cite}:{codes}\n")


def test_start_writes_the_run_files(config, project):
    context.init(config)
    (project / "context" / "documents" / "plan.md").write_text("aims", encoding="utf-8")
    info = postdoc.start(config, QUESTION)
    path = project / info["path"]
    assert info["run"] == path.name and info["harness"] == "claude-code" and info["model"] == "opus"
    request = (path / "request.md").read_text(encoding="utf-8")
    assert request.startswith("---\nquestion:") and request.rstrip().endswith(QUESTION)
    inputs = json.loads((path / "inputs.json").read_text(encoding="utf-8"))
    assert set(inputs["context"]) == {"context/context.md", "context/instructions.md"}
    assert list(inputs["documents"]) == ["context/documents/plan.md"]
    assert inputs["measurements"]["xrd:lab:S-9_1.2.26.xrdml"]["method"] == "xrd"
    assert inputs["labpipe_version"]
    assert json.loads((path / "dataset.json").read_text(encoding="utf-8"))["schema"] == "labpipe.dataset/1"
    assert postdoc.list_runs(config)[0]["state"] == "no analysis"


def test_verify_accepts_good_text(config, project):
    run = _run(config, project, _text(
        "a = 3.8822 ± 0.0030 Å (3.88221 in `phases.a_angstrom`) for S-9_1.2.26 at 2026-10-19, "
        "run 20261019T100000Z, 1 sample."))
    result = postdoc.verify(config, run)
    assert result["ok"] and result["state"] == "verified", result
    assert postdoc.list_runs(config)[0]["state"] == "verified"
    assert postdoc.latest(config)["verified"]["run"] == run


def test_verify_rejects_an_invented_number(config, project):
    run = _run(config, project, _text("a = 3.9500 Å for S-9_1.2.26."))
    result = postdoc.verify(config, run)
    assert not result["ok"] and [u["text"] for u in result["unmatched"]] == ["3.9500"]
    assert result["state"] == "retry" and result["attempts"] == 1


def test_numbers_of_unnamed_measurements_come_only_from_the_dataset(config, project):
    # 0.00297 is in results.json only (sigma key): found when the measurement is named
    named = _run(config, project, _text("sigma 0.00297 for S-9_1.2.26."))
    assert postdoc.verify(config, named)["ok"]
    unnamed = _run(config, project, _text("sigma 0.00297.", cite="none"))
    assert not postdoc.verify(config, unnamed)["ok"]


def test_compare_numbers_count_as_known(config, project):
    info = postdoc.start(config, QUESTION)
    path = project / info["path"]
    (path / "compare-1.json").write_text(json.dumps({"difference": {"S-9": 0.01234}}))
    (path / "analysis.md").write_text(_text("difference 0.0123 to the reference."))
    assert postdoc.verify(config, info["run"])["ok"]


def _paper(config, project, monkeypatch):
    context.init(config)
    (project / "context" / "documents" / "paper.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(context.shutil, "which", lambda name: "/usr/bin/pdftotext")
    monkeypatch.setattr(context.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout=b"a(Pd) = 3.8903 A\n\fa(Pt) = 3.9231 A\n\f", stderr=b""))
    context.index(config)


def test_literature_number_must_be_in_the_cited_document(config, project, monkeypatch):
    _paper(config, project, monkeypatch)
    bad = _run(config, project, _text("The reference is 3.9999 (`documents/paper.pdf` p. 1) here."))
    result = postdoc.verify(config, bad)
    assert not result["ok"] and "paper.pdf" in result["unmatched"][0]["reason"]
    wrong_page = _run(config, project, _text("Reference 3.9231, documents/paper.pdf p. 1."))
    assert not postdoc.verify(config, wrong_page)["ok"]
    good = _run(config, project, _text("Reference 3.9231, documents/paper.pdf p. 2."))
    assert postdoc.verify(config, good)["ok"]
    anywhere = _run(config, project, _text("Reference 3.8903, see documents/paper.pdf."))
    assert postdoc.verify(config, anywhere)["ok"]
    uncited = _run(config, project, _text("Reference 3.8903 from the literature."))
    assert not postdoc.verify(config, uncited)["ok"]


def test_verify_requires_review_codes_of_named_measurements(config, project):
    run = _run(config, project, _text("Looks fine for S-9_1.2.26.", codes=""))
    result = postdoc.verify(config, run)
    assert not result["ok"]
    assert result["missing_review_codes"] == {"xrd:lab:S-9_1.2.26.xrdml": ["wh_unreliable"]}
    unnamed = _run(config, project, _text("Looks fine.", codes="", cite="none"))
    assert postdoc.verify(config, unnamed)["ok"]


def test_verify_requires_the_three_parts(config, project):
    run = _run(config, project, "# Notes\n\nSome free text without numbers.\n")
    result = postdoc.verify(config, run)
    assert not result["ok"] and len(result["missing_parts"]) == 3
    other = _run(config, project, _text(question="Something else entirely?"))
    assert any("Question" in part for part in postdoc.verify(config, other)["missing_parts"])


def test_third_failed_attempt_fails_the_run(config, project):
    run = _run(config, project, _text("bad 9.99."))
    analysis = project / "analysis" / "_postdoc" / run / "analysis.md"
    states = []
    for number in ("9.99", "8.88", "7.77"):
        analysis.write_text(_text(f"bad {number}."), encoding="utf-8")
        states.append(postdoc.verify(config, run)["state"])
    assert states == ["retry", "retry", "failed"]
    analysis.write_text(_text("fixed, S-9_1.2.26."), encoding="utf-8")   # too late
    assert postdoc.verify(config, run)["state"] == "failed"
    verify_json = json.loads((analysis.parent / "verify.json").read_text(encoding="utf-8"))
    assert verify_json["attempts"] == 3 and verify_json["problems"]
    assert postdoc.list_runs(config)[0]["state"] == "failed"
    assert postdoc.latest(config)["newer"]["run"] == run


def test_same_text_is_not_a_new_attempt(config, project):
    run = _run(config, project, _text("bad 9.99."))
    assert postdoc.verify(config, run)["attempts"] == 1
    assert postdoc.verify(config, run)["attempts"] == 1


def test_editing_after_verification_makes_it_unverified(config, project):
    run = _run(config, project, _text("fine."))
    assert postdoc.verify(config, run)["ok"]
    (project / "analysis" / "_postdoc" / run / "analysis.md").write_text(_text("changed."))
    assert postdoc.list_runs(config)[0]["state"] == "unverified"


# ---- freshness -----------------------------------------------------------------------

def test_freshness_detects_changes(config, project):
    context.init(config)
    run = _run(config, project)
    assert postdoc.freshness(config, run)["up_to_date"]
    (project / "analysis/S-9/xrd/S-9_1.2.26/results.json").write_text('{"changed": 1}')
    (project / "context" / "context.md").write_text("new aims")
    (project / "config").mkdir()
    (project / "config" / "samples.yaml").write_text("S-9: {}")
    data = json.loads((project / "manifest.json").read_text())
    data["entries"]["eds:lab:S-9.emsa"] = {**_entry("eds:lab:S-9.emsa", "S-9", "analysis/S-9/eds/m"),
                                           "method": "eds"}
    (project / "analysis/S-9/eds/m").mkdir(parents=True)
    (project / "analysis/S-9/eds/m/results.json").write_text("{}")
    (project / "manifest.json").write_text(json.dumps(data))
    fresh = postdoc.freshness(config, run)
    assert not fresh["up_to_date"]
    assert fresh["changed_results"] == ["xrd:lab:S-9_1.2.26.xrdml"]
    assert fresh["new_measurements"] == [{"entry_id": "eds:lab:S-9.emsa", "sample_id": "S-9",
                                          "method": "eds"}]
    assert fresh["changed_context"] == ["context/context.md"]
    assert fresh["changed_settings"] == ["config/samples.yaml"]
    assert fresh["removed"] == []
    assert postdoc.list_runs(config)[0]["freshness"]["changed_results"] == 1


# ---- guard ---------------------------------------------------------------------------

def _write(path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def test_guard_context_and_postdoc_paths(config):
    root = PROJECT_ROOT
    for mode in ("interactive", "unattended", "postdoc"):
        assert guard.decide(_write(root / "context" / "documents" / "x.pdf"), config, mode)
        assert guard.decide(_write(root / "context" / ".text" / "x.txt"), config, mode)
    analysis = root / "analysis" / "_postdoc" / "20261019T100000Z" / "analysis.md"
    assert guard.decide(_write(analysis), config, "interactive") is None
    assert guard.decide(_write(analysis), config, "postdoc") is None
    assert guard.decide(_write(analysis), config, "unattended")
    assert guard.decide(_write(analysis.with_name("verify.json")), config, "interactive")
    assert guard.decide(_write(root / "context" / "context.md"), config, "postdoc")
    assert guard.decide(_write(root / "logs" / "x.md"), config, "postdoc")


def test_guard_postdoc_mode_commands(config):
    def bash(command):
        return {"tool_name": "Bash", "tool_input": {"command": command}}
    for ok in ("labpipe dataset compare --run X", "labpipe postdoc verify X", "labpipe xrd summary --entry e",
               "labpipe status", "cat context/context.md"):
        assert guard.decide(bash(ok), config, "postdoc") is None, ok
    for bad in ("labpipe postdoc run Q", "labpipe xrd analyze --entry e", "labpipe scan",
                "labpipe notes retire --entry e", "labpipe postdoc verify X; ls"):
        assert guard.decide(bash(bad), config, "postdoc"), bad


# ---- setup asks for models -----------------------------------------------------------

def test_setup_writes_the_chosen_models(project, monkeypatch):
    (project / "config").mkdir()
    (project / "config" / "pipeline.yaml").write_text(
        (PROJECT_ROOT / "config" / "pipeline.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (project / "config" / "local.yaml").write_text(
        "raw_roots:\n  - id: lab\n    path: /old\nagents:\n  harness: claude-code\n",
        encoding="utf-8")
    monkeypatch.setattr(wizard, "PROJECT_ROOT", project)
    monkeypatch.setattr(agents, "sync", lambda config: {"written": []})
    answers = iter(["claude-code", "", "sonnet", "opus"])   # Enter takes the recommendation
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    wizard.agent_models(wizard.load_pipeline_config())
    saved = wizard.load_pipeline_config()
    assert saved["agents"]["models"] == {"supervisor": "haiku", "analyst": "sonnet",
                                         "postdoc": "opus"}
    assert saved["raw_roots"][0]["path"] == "/old"


def test_setup_codex_asks_reasoning_and_inherit(project, monkeypatch):
    (project / "config").mkdir()
    (project / "config" / "pipeline.yaml").write_text(
        (PROJECT_ROOT / "config" / "pipeline.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (project / "config" / "local.yaml").write_text("raw_roots: []\n", encoding="utf-8")
    monkeypatch.setattr(wizard, "PROJECT_ROOT", project)
    monkeypatch.setattr(wizard, "_codex_models", lambda: [])
    monkeypatch.setattr(agents, "sync", lambda config: {"written": []})
    answers = iter(["codex"] + [""] * 6)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    wizard.agent_models(wizard.load_pipeline_config())
    saved = wizard.load_pipeline_config()["agents"]
    assert saved["models"]["postdoc"] == "inherit"
    assert saved["reasoning"] == {"supervisor": "low", "analyst": "medium", "postdoc": "high"}


def test_postdoc_role_renders_and_supervisor_cannot_delegate_to_it():
    roles = {r["name"]: r for r in agents.load_roles()}
    text, _ = agents._render(roles["postdoc"], "claude-code", {"postdoc": "opus"})
    assert "model: opus" in text and "Agent(" not in text
    assert "postdoc" not in agents._delegates(roles["supervisor"])


# ---- the headless way ----------------------------------------------------------------

def test_run_headless_builds_the_postdoc_command_and_notifies(config, project, monkeypatch):
    from labpipe import automation
    seen = {}
    monkeypatch.setattr(automation, "notify", lambda cfg, message, kind="info": seen.setdefault(
        "notes", []).append((kind, message)) or [])
    monkeypatch.setattr(automation.shutil, "which", lambda name: "/usr/bin/claude")

    def fake_run(command, **kwargs):
        seen["command"], seen["env"] = command, kwargs["env"]
        run = next(p for p in (project / "analysis" / "_postdoc").iterdir())
        (run / "analysis.md").write_text(_text("fine."), encoding="utf-8")
        kwargs["stdout"].write(json.dumps({"type": "result", "total_cost_usd": 1.5}) + "\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(postdoc.subprocess, "run", fake_run)
    result = postdoc.run_headless(config, QUESTION, budget_usd=7)
    assert result["status"] == "finished" and result["state"] == "verified"
    assert result["cost_usd"] == 1.5
    command = seen["command"]
    assert command[command.index("--agent") + 1] == "postdoc"
    assert command[command.index("--max-budget-usd") + 1] == "7"
    assert seen["env"]["LABPIPE_AGENT_MODE"] == "postdoc"
    assert [kind for kind, _ in seen["notes"]] == ["started", "finished"]


# ---- report --------------------------------------------------------------------------

def _section(config, monkeypatch, project):
    from labpipe import automation, report
    monkeypatch.setattr(report, "PROJECT_ROOT", project)
    monkeypatch.setattr(automation, "PROJECT_ROOT", project)
    return report._postdoc_section(config, {"S-9_1.2.26": "S-9_1.2.26", "S-9": "sample-S-9"})


def test_report_says_when_the_postdoc_was_never_asked(config, project, monkeypatch):
    section = _section(config, monkeypatch, project)
    assert "The postdoc has not been asked anything yet" in section
    assert "Open" not in section and "open Claude Code in" in section
    assert "ask the postdoc: &lt;your question&gt;" in section


def test_report_shows_the_verified_analysis_with_links(config, project, monkeypatch):
    run = _run(config, project, _text("S-9 has a = 3.8822 Å (S-9_1.2.26)."))
    assert postdoc.verify(config, run)["ok"]
    section = _section(config, monkeypatch, project)
    assert "compare with the reference?" in section and "Out of date" not in section and "warnbox" not in section
    assert '<a href="#sample-S-9">S-9</a>' in section
    assert '<a href="#S-9_1.2.26">S-9_1.2.26</a>' in section


def test_report_says_when_the_analysis_is_out_of_date(config, project, monkeypatch):
    run = _run(config, project, _text())
    assert postdoc.verify(config, run)["ok"]
    (project / "analysis/S-9/xrd/S-9_1.2.26/results.json").write_text('{"changed": 1}')
    section = _section(config, monkeypatch, project)
    assert "Out of date:</strong> 1 result(s) changed" in section


def test_report_shows_a_newer_failed_run_and_the_last_good_one(config, project, monkeypatch):
    good = _run(config, project, _text())
    assert postdoc.verify(config, good)["ok"]
    bad = _run(config, project, _text("a = 3.9500 Å"), question="Which samples again?")
    analysis = project / "analysis" / "_postdoc" / bad / "analysis.md"
    for n in range(3):
        analysis.write_text(_text(f"a = 3.95{n}0 Å", question="Which samples again?"))
        postdoc.verify(config, bad)
    section = _section(config, monkeypatch, project)
    assert "<em>Which samples again?</em>) did not pass its checks: 1 number(s)" in section
    assert "The last one that passed is below." in section
    assert "compare with the reference?" in section
