"""notes verify, the guard hook and doctor: the deterministic parts the agents rely on."""

from __future__ import annotations

from pathlib import Path

import pytest

from labpipe import guard
from labpipe.notes import numbers_in_notes, verify
from labpipe.safety import PROJECT_ROOT

RESULTS = {
    "fit": {"global": {"rwp": 0.036213, "chi2_reduced": 1.84}},
    "phases": {"fcc_indexed": [{"a_angstrom": 3.88221, "a_sigma_angstrom": 0.00297,
                                "n_reflections": 4}]},
    "checks": [{"code": "wh_unreliable", "severity": "review", "message": "R² 0.31"},
               {"code": "no_instrument_correction", "severity": "warning", "message": ""}],
}
SHA = "a" * 64


def _notes(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "notes.md"
    path.write_text(f"# XRD — S-9 / S-9_1.2.26\n\nresults.json sha256 `{SHA}`\n\n{body}",
                    encoding="utf-8")
    return path


def test_verify_accepts_copied_and_rounded_numbers(tmp_path):
    notes = _notes(tmp_path, "a = 3.8822 ± 0.0030 Å from 4 reflections (111), (200); "
                             "Rwp 0.036 (2026-10-16), Kα2 modelled, 2θ range.\n"
                             "- `wh_unreliable`: R² 0.31\n")
    result = verify(notes, RESULTS, None, SHA)
    assert result["ok"], result
    assert {m["text"] for m in result["matched"]} == {"3.8822", "0.0030", "4", "0.036", "0.31"}


def test_verify_rejects_invented_or_converted_numbers(tmp_path):
    notes = _notes(tmp_path, "Rwp 3.6 % and a = 3.8830 Å. `wh_unreliable`\n")
    result = verify(notes, RESULTS, None, SHA)
    assert not result["ok"]
    assert {u["text"] for u in result["unmatched"]} == {"3.6", "3.8830"}


def test_verify_requires_review_codes_and_matching_sha(tmp_path):
    notes = _notes(tmp_path, "a = 3.882 Å\n")
    result = verify(notes, RESULTS, None, "b" * 64)
    assert not result["ok"]
    assert any("wh_unreliable" in p for p in result["problems"])
    assert any("sha256" in p for p in result["problems"])


def test_identifiers_are_not_numbers():
    found = numbers_in_notes("S-17_3.2.26 RuO2 P03 Kα1 `phases.fcc_indexed[0]` (2 2 0)\n"
                             "1. first item\n## 2. heading")
    assert found == []


@pytest.fixture
def config(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    return {"raw_roots": [{"id": "lab", "path": str(raw)}], "protected_paths": [str(tmp_path / "sync")],
            "analysis_dir": "analysis", "logs_dir": "logs", "manifest": "manifest.json"}


def _write(path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def _bash(command) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_guard_blocks_raw_and_pipeline_outputs(config, tmp_path):
    raw = Path(config["raw_roots"][0]["path"])
    assert guard.decide(_write(raw / "x.xrdml"), config, "interactive")
    assert guard.decide(_write(tmp_path / "sync" / "a" / "b.txt"), config, "interactive")
    assert guard.decide(_write(PROJECT_ROOT / "manifest.json"), config, "interactive")
    assert guard.decide(_write(PROJECT_ROOT / "analysis" / "S-1" / "xrd" / "m" / "results.json"),
                        config, "interactive")
    assert guard.decide(_write(PROJECT_ROOT / "analysis" / "S-1" / "xrd" / "m" / "notes.md"),
                        config, "interactive") is None
    assert guard.decide(_write(PROJECT_ROOT / "config" / "samples.yaml"), config,
                        "interactive") is None
    assert guard.decide(_bash(f"ls '{raw}'"), config, "interactive")


def test_guard_unattended_allows_only_labpipe_and_notes(config):
    assert guard.decide(_bash("labpipe scan"), config, "unattended") is None
    assert guard.decide(_bash("labpipe scan && rm -rf analysis"), config, "unattended")
    assert guard.decide(_bash("labpipe status | head"), config, "unattended")
    assert guard.decide(_bash("python -c 1"), config, "unattended")
    assert guard.decide(_write(PROJECT_ROOT / "config" / "samples.yaml"), config, "unattended")
    assert guard.decide(_write(PROJECT_ROOT / "logs" / "runs" / "s.md"), config,
                        "unattended") is None


def test_doctor_reports_without_writing(tmp_path, monkeypatch):
    from labpipe import doctor
    monkeypatch.setattr(doctor, "load_pipeline_config", lambda: (_ for _ in ()).throw(
        doctor.SetupError("missing")))
    result = doctor.run()
    assert result["ready"] is False
    assert any(c["check"] == "local_config" and c["status"] == "todo" for c in result["checks"])


def test_readonly_view_commands_escape_paths():
    from labpipe.roview import _fstab_escape, commands
    assert _fstab_escape("/data/My Lab/raw") == "/data/My\\040Lab/raw"
    linux = commands("/data/My Lab/raw", "/srv/ro", system="linux")
    assert "sudo mount --bind '/data/My Lab/raw' /srv/ro" in linux["steps"][0]["commands"]
    assert linux["then"]["config/local.yaml"]["raw_roots"][0]["path"] == "/srv/ro"
    windows = commands("D:\\Lab Data\\raw", None, system="windows")
    assert "-Path 'D:\\Lab Data\\raw'" in windows["steps"][0]["commands"][0]
    assert windows["then"]["config/local.yaml"]["protected_paths"] == ["D:/Lab Data/raw"]


def test_agents_render_per_harness():
    from labpipe import agents
    role = {"name": "demo", "role": "analyst", "description": "d", "source": "demo.md",
            "capabilities": ["shell", "read", "write"], "body": "Body\n", "text": "x",
            "harness_extras": {"claude-code": {"maxTurns": 30}}}
    claude, _ = agents._render(role, "claude-code", {"analyst": "anthropic/claude-sonnet-5-5"})
    assert "tools: Bash, Read, Write" in claude and "model: claude-sonnet-5-5" in claude
    assert "maxTurns: 30" in claude and claude.rstrip().endswith("Body")
    opencode, _ = agents._render(role, "opencode", {"analyst": "openai/gpt-5"})
    assert "model: openai/gpt-5" in opencode and "labpipe *: allow" in opencode
    assert "maxTurns" not in opencode
    with pytest.raises(agents.AgentConfigError):
        agents._render(role, "claude-code", {"analyst": "openai/gpt-5"})


def test_guard_unattended_allows_only_pipeline_subcommands(config):
    for allowed in ("labpipe notes verify --entry x", "labpipe xrd summary --entry x",
                    "labpipe report"):
        assert guard.decide(_bash(allowed), config, "unattended") is None, allowed
    for blocked in ("labpipe agents sync", "labpipe requeue --stale", "labpipe init",
                    "labpipe notes retire --entry x",
                    "labpipe xrd refs add a.cif --id X --structure-type fcc"):
        assert guard.decide(_bash(blocked), config, "unattended"), blocked


@pytest.fixture
def local_project(tmp_path, monkeypatch):
    """A throwaway project root with pipeline.yaml and a local.yaml for the setup writers."""
    from labpipe import safety, wizard
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pipeline.yaml").write_text(
        (PROJECT_ROOT / "config" / "pipeline.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config" / "local.yaml").write_text(
        "# head comment\nraw_roots:\n  - id: lab\n    path: /old\n\n"
        "# protected comment\nprotected_paths:\n  - /old\nagents:\n  harness: claude-code\n",
        encoding="utf-8")
    monkeypatch.setattr(safety, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(wizard, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_set_local_block_replaces_in_place(local_project):
    from labpipe.safety import set_local_block
    set_local_block("raw_roots", [{"id": "lab", "path": "/new"}])
    set_local_block("automation", {"settle_minutes": 3})
    text = (local_project / "config" / "local.yaml").read_text(encoding="utf-8")
    assert text.startswith("# head comment\nraw_roots:\n- id: lab\n  path: /new\n\n"
                           "# protected comment\nprotected_paths:\n  - /old\n")
    assert "/old\n" in text and text.endswith("automation:\n  settle_minutes: 3\n")
    assert "harness: claude-code" in text


def test_setup_raw_data_accepts_a_read_only_folder(local_project, tmp_path_factory, monkeypatch):
    from labpipe import wizard
    raw = tmp_path_factory.mktemp("raw")
    monkeypatch.setattr(wizard, "_read_only", lambda path: (Path(path) == raw, "test"))
    answers = iter(["y", str(raw), "lab"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    wizard.raw_data(wizard.load_pipeline_config())
    config = wizard.load_pipeline_config()
    assert config["raw_roots"] == [{"id": "lab", "path": str(raw)}]
    assert config["protected_paths"] == ["/old"]


def test_setup_agents_rejects_a_foreign_model_without_saving(local_project, monkeypatch):
    from labpipe import wizard
    before = (local_project / "config" / "local.yaml").read_text(encoding="utf-8")
    answers = iter(["claude-code", "openai/gpt-5", "sonnet"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    wizard.agent_models(wizard.load_pipeline_config())
    assert (local_project / "config" / "local.yaml").read_text(encoding="utf-8") == before


def test_setup_needs_a_terminal(monkeypatch):
    from labpipe import wizard
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert wizard.run() == 2
