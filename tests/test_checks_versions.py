"""The checks on notes and postdoc analyses are versioned: stricter rules apply to new text only.
(tests/test_postdoc.py and tests/test_report.py have the postdoc's and the report's side.)"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import textwrap

import pytest

from labpipe import manifest, notes, postdoc, safety

# ---- the guard against forgetting a bump ---------------------------------------------------
#
# The rule code is fingerprinted: the source of the functions and the patterns that decide
# whether notes (or a postdoc analysis) pass, without comments, docstrings and blank lines (so
# the value does not depend on the Python version). When it changes, this test fails: if the
# change makes the rules stricter, bump CHECKS_VERSION (notes.py or postdoc.py), then paste
# the new fingerprint below. A change that only restructures needs just the new fingerprint.

NOTES_FINGERPRINT = "a1a03c7eb200e34cc3950839f243e66c533484218f46826f57a4954f0ada9b5d"
POSTDOC_FINGERPRINT = "c7e3a1d586051f65ec2f7c0f5de2d8eb340a81db69711cfa816aac9b74e60b92"

SHARED = [notes.numbers_in_notes, notes._matches, notes._walk_numbers, notes._decimals]
SHARED_PATTERNS = [notes._NUMBER, *notes._EXEMPT, notes._SHA]


def _source(function) -> str:
    source = textwrap.dedent(inspect.getsource(function))
    node = ast.parse(source).body[0]
    drop = set()
    if ast.get_docstring(node, clean=False) is not None:
        drop = set(range(node.body[0].lineno, node.body[0].end_lineno + 1))
    lines = [line.strip() for number, line in enumerate(source.splitlines(), 1)
             if number not in drop]
    return "\n".join(line for line in lines if line and not line.startswith("#"))


def fingerprint(functions, patterns, extra="") -> str:
    parts = [_source(f) for f in functions]
    parts += [f"{p.pattern}|{p.flags}" for p in patterns]
    return hashlib.sha256("\n\x00".join([*parts, extra]).encode("utf-8")).hexdigest()


def notes_fingerprint() -> str:
    return fingerprint([notes.verify, *SHARED], SHARED_PATTERNS)


def postdoc_fingerprint() -> str:
    return fingerprint([postdoc._check, postdoc._mask, postdoc._named, postdoc._review_codes,
                        postdoc._document_numbers, *SHARED],
                       [*SHARED_PATTERNS, *postdoc._MASK, postdoc._DOC_REF, postdoc._PAGE_MARK],
                       json.dumps(postdoc.PARTS, sort_keys=True))


def test_the_notes_rules_did_not_change_without_a_new_checks_version():
    assert notes_fingerprint() == NOTES_FINGERPRINT, (
        "the notes rules changed: if they are stricter, bump notes.CHECKS_VERSION "
        f"(now {notes.CHECKS_VERSION}); then update the fingerprint here to "
        f"{notes_fingerprint()}")


def test_the_postdoc_rules_did_not_change_without_a_new_checks_version():
    assert postdoc_fingerprint() == POSTDOC_FINGERPRINT, (
        "the postdoc rules changed: if they are stricter, bump postdoc.CHECKS_VERSION "
        f"(now {postdoc.CHECKS_VERSION}); then update the fingerprint here to "
        f"{postdoc_fingerprint()}")


def test_the_fingerprint_ignores_comments_and_docstrings_but_sees_code():
    def first():
        def f(x):
            """Doc."""
            # comment
            return x + 1
        return f

    def second():
        def f(x):
            """Other doc."""

            return x + 1
        return f

    def third():
        def f(x):
            return x + 2
        return f
    assert fingerprint([first()], []) == fingerprint([second()], [])
    assert fingerprint([first()], []) != fingerprint([third()], [])


# ---- notes: the version is recorded, and older passes stay valid ----------------------------

RESULTS_SHA = "a" * 64
NOTES = f"Lattice parameter 3.88 (results.json sha256 {RESULTS_SHA}).\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (safety, manifest, notes):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(notes, "load_methods", lambda: {"xrd": {}})
    out = tmp_path / "analysis" / "S-1" / "xrd" / "S-1_a"
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps({"a": 3.88}), encoding="utf-8", newline="")
    (out / "notes.md").write_text(NOTES, encoding="utf-8", newline="")
    entry = {"entry_id": "xrd:lab:S-1_a.xrdml", "method": "xrd", "sample_id": "S-1",
             "measurement_id": "S-1_a", "status": "done", "results_sha256": RESULTS_SHA,
             "output_dir": "analysis/S-1/xrd/S-1_a"}
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"schema": "labpipe.manifest/1", "entries": {entry["entry_id"]: entry}}), encoding="utf-8", newline="")
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def config():
    return {"raw_roots": [], "protected_paths": [], "analysis_dir": "analysis",
            "logs_dir": "logs", "manifest": "manifest.json"}


EID = "xrd:lab:S-1_a.xrdml"


def _stricter(monkeypatch, version=3):
    """A new release whose rules refuse these notes."""
    real = notes.verify

    def verify(*args, **kwargs):
        result = real(*args, **kwargs)
        return {**result, "ok": False, "problems": ["a new rule is not met"],
                "rules": version}
    monkeypatch.setattr(notes, "CHECKS_VERSION", version)
    monkeypatch.setattr(notes, "verify", verify)


def _entry(project):
    return json.loads((project / "manifest.json").read_text(encoding="utf-8"))["entries"][EID]


def test_a_passing_verify_records_the_rules_in_the_manifest(project, config):
    result = notes.verify_entry(config, EID)
    assert result["ok"] and result["rules"] == notes.CHECKS_VERSION and "checked_with" not in result
    notes.record_check(config, result)
    check = _entry(project)["notes_check"]
    assert check["rules"] == notes.CHECKS_VERSION == 2
    assert check["notes_sha256"] == hashlib.sha256(NOTES.encode("utf-8")).hexdigest()
    assert check["results_sha256"] == RESULTS_SHA and check["labpipe"] and check["utc"]


def test_the_command_records_only_passes_under_the_current_rules(project, config, monkeypatch,
                                                                 capsys):
    from labpipe import cli
    monkeypatch.setattr(cli, "_refresh_report", lambda config: None)
    (project / "analysis" / "S-1" / "xrd" / "S-1_a" / "notes.md").write_text(
        "Lattice parameter 9.99 " + RESULTS_SHA, encoding="utf-8", newline="")
    args = cli.build_parser().parse_args(["notes", "verify", "--entry", EID])
    assert cli.cmd_notes_verify(args, config) == 1
    assert "notes_check" not in _entry(project)          # a failure records nothing
    (project / "analysis" / "S-1" / "xrd" / "S-1_a" / "notes.md").write_text(
        NOTES, encoding="utf-8", newline="")
    assert cli.cmd_notes_verify(args, config) == 0
    assert _entry(project)["notes_check"]["rules"] == notes.CHECKS_VERSION
    capsys.readouterr()


def test_notes_that_passed_under_older_rules_stay_valid(project, config, monkeypatch):
    notes.record_check(config, notes.verify_entry(config, EID))
    _stricter(monkeypatch)
    result = notes.verify_entry(config, EID)
    assert result["ok"] is True and result["problems"] == []
    assert result["checked_with"] == {"rules": 2, "labpipe": _entry(project)["notes_check"]["labpipe"]}
    assert result["problems_under_current_rules"] == ["a new rule is not met"]


def test_changed_text_or_results_or_unrecorded_notes_do_not_get_the_old_pass(
        project, config, monkeypatch):
    notes.record_check(config, notes.verify_entry(config, EID))
    _stricter(monkeypatch)
    path = project / "analysis" / "S-1" / "xrd" / "S-1_a" / "notes.md"
    path.write_text(NOTES + "A new sentence.\n", encoding="utf-8", newline="")        # other text
    assert notes.verify_entry(config, EID)["ok"] is False
    path.write_text(NOTES, encoding="utf-8", newline="")
    assert notes.verify_entry(config, EID)["ok"] is True
    manifest_file = project / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["entries"][EID]["results_sha256"] = "b" * 64                     # other results
    manifest_file.write_text(json.dumps(data), encoding="utf-8", newline="")
    assert notes.verify_entry(config, EID)["ok"] is False
    data["entries"][EID]["results_sha256"] = RESULTS_SHA
    del data["entries"][EID]["notes_check"]                               # never recorded
    manifest_file.write_text(json.dumps(data), encoding="utf-8", newline="")
    assert notes.verify_entry(config, EID)["ok"] is False


def test_a_pass_under_the_same_rules_is_not_an_older_pass(project, config, monkeypatch):
    """A failure under the current rules is a failure; only older rules are forgiven."""
    notes.record_check(config, notes.verify_entry(config, EID))
    real = notes.verify
    monkeypatch.setattr(notes, "verify", lambda *a, **k: {
        **real(*a, **k), "ok": False, "problems": ["nope"]})
    assert notes.verify_entry(config, EID)["ok"] is False


def test_notes_todo_never_lists_a_note_only_because_the_rules_changed(project, config,
                                                                      monkeypatch):
    assert notes.todo(config) == []
    _stricter(monkeypatch)
    assert notes.verify_entry(config, EID)["ok"] is False      # fails the new rules ...
    assert notes.todo(config) == []                            # ... and is still not queued
    (project / "analysis" / "S-1" / "xrd" / "S-1_a" / "notes.md").write_text(
        "Some other results 1111111111111111111111111111111111111111111111111111111111111111",
        encoding="utf-8", newline="")
    assert [row["entry_id"] for row in notes.todo(config)] == [EID]   # (other results: queued)


# ---- rules 2: inline code is checked like the rest of the text ------------------------------

def _write_notes(project, text):
    path = project / "analysis" / "S-1" / "xrd" / "S-1_a" / "notes.md"
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_a_number_in_backticks_must_be_in_the_results(project, config):
    _write_notes(project, f"Lattice `0.55` and `a = 0.77 nm` ({RESULTS_SHA}).\n")
    result = notes.verify_entry(config, EID)
    assert not result["ok"]
    assert [u["text"] for u in result["unmatched"]] == ["0.55", "0.77"]
    _write_notes(project, f"Lattice `3.88` ({RESULTS_SHA}).\n")
    assert notes.verify_entry(config, EID)["ok"]


def test_identifiers_and_key_paths_in_backticks_are_still_not_numbers(project, config):
    _write_notes(project, f"""Sample `S-9`, `RuO2`, entry `xrd-1a2b3c` and `xrd:lab:S-1_a.xrdml`,
keys `xrd.a.fcc`, `fit.peaks[3].center`, `peaks[0]`, check `wh_unreliable`, sha `{RESULTS_SHA}`.
Lattice 3.88 (results.json sha256 {RESULTS_SHA}).
""")
    result = notes.verify_entry(config, EID)
    assert result["ok"], result["unmatched"]
    assert result["numbers_checked"] == 1
    _write_notes(project, f"Hidden `x[3]` is an index; but prose peaks[7] and `7` are numbers "
                          f"({RESULTS_SHA}).\n")
    assert [u["text"] for u in notes.verify_entry(config, EID)["unmatched"]] == ["7", "7"]


def test_notes_that_passed_under_rules_1_with_a_number_in_backticks_stay_valid(project, config):
    text = f"Lattice 3.88 and `0.55` ({RESULTS_SHA}).\n"
    path = _write_notes(project, text)
    assert not notes.verify_entry(config, EID)["ok"]            # not under the current rules
    manifest_file = project / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["entries"][EID]["notes_check"] = {
        "rules": 1, "labpipe": "v0.3.0", "results_sha256": RESULTS_SHA,
        "notes_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "utc": "2026-01-01T00:00:00Z"}
    manifest_file.write_text(json.dumps(data), encoding="utf-8", newline="")
    result = notes.verify_entry(config, EID)
    assert result["ok"] and result["checked_with"] == {"rules": 1, "labpipe": "v0.3.0"}
    assert result["problems_under_current_rules"]
    assert notes.todo(config) == []
    _write_notes(project, text.replace("0.55", "0.56"))         # changed text: no old pass
    assert not notes.verify_entry(config, EID)["ok"]
