"""The lab's own methods (lab/methods.yaml, lab/methods/<name>/): scaffold, import, registry,
versions, replay, report, agents, container forwarding. Everything runs in a tmp project; the
code is the repository's, and nothing of it is edited."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from labpipe import labmethods
from labpipe.cli import build_parser
from labpipe.registry import shipped_methods
from tests.test_pipeline import REPO, _labpipe, _project


def _example(raw: Path, name: str = "D-1_a.dat") -> Path:
    path = raw / "DEMO" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    x = [float(i) for i in range(20)]
    path.write_text("# x y\n" + "\n".join(f"{v} {10 - (v - 7) ** 2 / 10}" for v in x) + "\n",
                    encoding="utf-8")
    return path


def _scaffold(tmp_path, **kwargs):
    project, raw, env = _project(tmp_path)
    example = _example(raw)
    made = _labpipe(env, "methods", "new", "demo", "--example", str(example),
                    "--description", "Demo technique", *kwargs.get("extra", []))
    return project, raw, env, example, made


def test_methods_new_in_a_project_writes_only_into_lab(tmp_path):
    project, raw, env, example, made = _scaffold(tmp_path)
    assert sorted(made["created"]) == sorted([
        "lab/methods/demo/__init__.py", "lab/methods/demo/analyze.py",
        "lab/methods/demo/config.yaml", "lab/methods/demo/README.md",
        "lab/agents/demo-analyst.md", "lab/methods.yaml (entry)"])
    assert not (REPO / "src" / "labpipe" / "methods" / "demo").exists()
    assert not (REPO / "config" / "demo.yaml").exists()
    analyze = (project / "lab/methods/demo/analyze.py").read_text(encoding="utf-8")
    assert 'METHOD = "demo"' in analyze and "labpipe_lab.demo.analyze.run" in analyze
    role = (project / "lab/agents/demo-analyst.md").read_text(encoding="utf-8")
    assert "name: demo-analyst" in role and "lab/methods/demo/README.md" in role and "{{" not in role
    entry = yaml.safe_load((project / "lab/methods.yaml").read_text(encoding="utf-8"))["methods"]["demo"]
    assert entry["module"] == "labpipe_lab.demo.analyze"
    assert entry["config"] == "lab/methods/demo/config.yaml" and entry["subagent"] == "demo-analyst"
    again = _labpipe(env, "methods", "new", "demo", "--example", str(example), "--description", "x")
    assert "already" in again["error"]
    for reserved in ("scan", "xrd"):
        bad = _labpipe(env, "methods", "new", reserved, "--example", str(example),
                       "--description", "x")
        assert "error" in bad
    # a second method keeps the first one's entry
    _labpipe(env, "methods", "new", "other", "--example", str(example), "--description", "Other")
    names = yaml.safe_load((project / "lab/methods.yaml").read_text(encoding="utf-8"))["methods"]
    assert list(names) == ["demo", "other"]


def test_a_lab_method_scans_analyses_reports_versions_and_replays(tmp_path):
    project, raw, env, example, _ = _scaffold(tmp_path)
    checked = _labpipe(env, "methods", "check", "demo", "--example", str(example))
    assert checked["ok"] and "Reading the plot" in checked["warnings"][0]   # warns, does not fail
    page = project / "lab/methods/demo/README.md"
    page.write_text(page.read_text(encoding="utf-8").replace(
        "## Reading the plot\n", "## Reading the plot\n- x is time (s), y the signal (V).\n"),
        encoding="utf-8")
    assert "warnings" not in _labpipe(env, "methods", "check", "demo", "--example", str(example))
    assert (project / "logs" / "method-check").is_dir()
    assert _labpipe(env, "doctor")["checks"]            # reads fine with a lab method
    _labpipe(env, "scan")
    entry = _pending_demo(env)
    assert entry["sample_id"] == "D-1"
    done = _labpipe(env, "demo", "analyze", "--entry", entry["entry_id"])
    assert done["status"] == "done", done
    out = project / done["output_dir"]
    record = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert record["code"]["labpipe_entry"] == "labpipe_lab.demo.analyze.run"
    assert record["code"]["analysis_version"] and record["code"]["plot_version"]
    sources = record["parameters"]["sources"][0]
    assert sources["root"] == "project" and sources["file"] == "lab/methods/demo/config.yaml"
    # versions: listed, and the closure holds the lab module
    listed = _labpipe(env, "methods", "versions")["demo"]
    assert "labpipe_lab.demo.analyze" in listed["analysis_modules"]
    assert record["code"]["analysis_version"] == listed["analysis_version"]
    # report and status see it
    assert _labpipe(env, "report")
    html = (project / "analysis" / "report.html").read_text(encoding="utf-8")
    assert "D-1" in html and "demo" in html
    assert _labpipe(env, "status", entry["entry_id"])["status"] == "done"
    # replay regenerates identical files
    replay = _labpipe(env, "replay", str(out / "provenance.json"))
    assert replay["all_outputs_identical"], replay
    # changing the lab method's code changes its version and replay refuses
    analyze = project / "lab/methods/demo/analyze.py"
    analyze.write_text(analyze.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    assert _labpipe(env, "methods", "versions")["demo"]["analysis_version"] != \
        listed["analysis_version"]
    assert _labpipe(env, "replay", str(out / "provenance.json"))["error"] == "replay refused"
    _labpipe(env, "scan")
    assert _labpipe(env, "status", entry["entry_id"])["status"] == "stale"


def _pending_demo(env) -> dict:
    return next(e for e in _labpipe(env, "pending") if e["method"] == "demo")


def _cli(env, *args) -> tuple[int, str]:
    done = subprocess.run([sys.executable, "-m", "labpipe.cli", *args], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert "Traceback" not in done.stderr, done.stderr
    return done.returncode, done.stdout


def test_summary_and_quantities_of_a_lab_method(tmp_path):
    project, raw, env, example, _ = _scaffold(tmp_path)
    (project / "lab/methods/demo/summary.py").write_text(
        '"""Summary of demo."""\n\n'
        "def summarize(results):\n"
        '    return {"headline": f"maximum {results[\'summary\'][\'y_max\']:.2f}"}\n\n'
        "def quantities(results):\n"
        '    return {"demo.max": {"value": results["summary"]["y_max"], "sigma": None,\n'
        '                          "unit": "a.u.", "key": "summary.y_max"}}\n', encoding="utf-8")
    registry = project / "lab/methods.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace(
        "    module: labpipe_lab.demo.analyze",
        "    module: labpipe_lab.demo.analyze\n    summary: labpipe_lab.demo.summary"),
        encoding="utf-8")
    _labpipe(env, "scan")
    entry = _pending_demo(env)
    _labpipe(env, "demo", "analyze", "--entry", entry["entry_id"])
    summary = _labpipe(env, "demo", "summary", "--entry", entry["entry_id"])
    assert summary["headline"].startswith("maximum")
    dataset = _labpipe(env, "dataset")
    assert "demo.max" in json.dumps(dataset)
    assert _labpipe(env, "methods", "check", "demo", "--example", str(example))["ok"]


def test_a_lab_method_may_not_reuse_a_shipped_name(tmp_path):
    project, raw, env = _project(tmp_path)
    (project / "lab").mkdir()
    (project / "lab/methods.yaml").write_text(
        "methods:\n  xrd:\n    patterns: ['**/*.zzz']\n    module: labpipe_lab.xrd.analyze\n"
        "    config: lab/methods/xrd/config.yaml\n", encoding="utf-8")
    status, out = _cli(env, "scan")
    error = json.loads(out)["error"]
    assert status == 2 and "'xrd'" in error and "lab/methods.yaml" in error and "shipped" in error
    doctor = _labpipe(env, "doctor")
    item = next(c for c in doctor["checks"] if c["check"] == "lab_methods")
    assert item["status"] == "todo" and "lab/methods.yaml" in item["message"]
    assert doctor["ready"] is False


def test_a_broken_lab_methods_file_is_an_error_not_a_traceback(tmp_path):
    project, raw, env = _project(tmp_path)
    (project / "lab").mkdir()
    registry = project / "lab/methods.yaml"
    for text, fragment in (
            ("methods: [unclosed\n", "cannot be read"),
            ("methods:\n  demo: 3\n", "demo"),
            ("methods:\n  demo:\n    patterns: ['*.x']\n", "`module` is missing"),
            ("methods:\n  Bad-Name:\n    patterns: ['*.x']\n", "lower-case"),
            ("methods:\n  demo:\n    patterns: ['*.x']\n    module: labpipe.methods.xrd.analyze\n"
             "    config: c.yaml\n", "must start with labpipe_lab.demo"),
            ("methods:\n  scan:\n    patterns: ['*.x']\n    module: labpipe_lab.scan.a\n"
             "    config: c.yaml\n", "name of a labpipe command")):
        registry.write_text(text, encoding="utf-8")
        for command in (("scan",), ("inventory",), ("doctor",)):
            status, out = _cli(env, *command)
            assert status in (1, 2) and fragment in out, (text, command, out)


def test_patterns_of_lab_methods_come_after_the_shipped_ones(tmp_path, monkeypatch):
    from labpipe import registry
    project, raw, env = _project(tmp_path)
    (project / "lab").mkdir()
    (project / "lab/methods.yaml").write_text(
        "methods:\n  mine:\n    patterns: ['**/*.xrdml', '**/*.mine']\n"
        "    module: labpipe_lab.mine.analyze\n    config: lab/methods/mine/config.yaml\n",
        encoding="utf-8")
    monkeypatch.setattr(labmethods.safety, "PROJECT_ROOT", project)
    methods = registry.load_methods()
    assert list(methods)[-1] == "mine" and methods["mine"]["lab"] is True
    assert "lab" not in methods["xrd"]
    assert registry.classify("a/b.xrdml", methods) == ("xrd", "analyse")      # shipped first
    assert registry.classify("a/b.mine", methods) == ("mine", "analyse")


def test_the_lab_package_follows_the_project(tmp_path, monkeypatch):
    import importlib
    first, second = tmp_path / "one", tmp_path / "two"
    for folder, value in ((first, 1), (second, 2)):
        package = folder / "lab" / "methods" / "m"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
    monkeypatch.setattr(labmethods.safety, "PROJECT_ROOT", first)
    labmethods.register()
    assert importlib.import_module("labpipe_lab.m").VALUE == 1
    monkeypatch.setattr(labmethods.safety, "PROJECT_ROOT", second)
    labmethods.register()
    assert importlib.import_module("labpipe_lab.m").VALUE == 2


def test_reserved_names_are_the_cli_commands():
    commands = set(build_parser()._subparsers._group_actions[0].choices) - set(shipped_methods())
    assert commands <= labmethods.RESERVED, sorted(commands - labmethods.RESERVED)


def test_project_sync_tells_agents_where_the_lab_methods_are(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("LABPIPE_")}
    environment.update(PYTHONPATH=str(REPO / "src"), HOME=str(home), USERPROFILE=str(home))
    folder = tmp_path / "proj"
    data = tmp_path / "data"
    data.mkdir()

    def run(*args):
        done = subprocess.run([sys.executable, "-m", "labpipe.cli", *args], cwd=folder if
                              folder.exists() else tmp_path, env=environment, capture_output=True,
                              text=True, encoding="utf-8", timeout=600)
        assert done.returncode == 0, done.stdout + done.stderr
        return json.loads(done.stdout)

    run("project", "new", str(folder), "--data", str(data))
    assert "## This lab's own methods" not in (folder / "AGENTS.md").read_text(encoding="utf-8")
    example = _example(tmp_path / "ex")
    run("methods", "new", "demo", "--example", str(example), "--description", "Demo technique")
    run("project", "sync")
    agents_file = (folder / "AGENTS.md").read_text(encoding="utf-8")
    assert "This lab's own methods (lab/methods.yaml)" in agents_file
    assert "`lab/methods/demo/README.md`" in agents_file and "`lab/agents/demo-analyst.md`" in agents_file
    assert (folder / ".claude" / "agents" / "demo-analyst.md").is_file()   # the analyst role
    supervisor = (folder / ".claude" / "agents" / "supervisor.md").read_text(encoding="utf-8")
    assert "demo-analyst" in supervisor


def test_reading_the_plot_must_have_bullets(tmp_path):
    from labpipe.methods_kit import reading_the_plot_missing
    page = tmp_path / "m.md"
    page.write_text("# m\n\n## Input\n- x\n", encoding="utf-8")
    assert "no" in reading_the_plot_missing(page)
    page.write_text("## Reading the plot\n<!-- fill in -->\n-\n\n## Settings\n- a\n", encoding="utf-8")
    assert "no bullets" in reading_the_plot_missing(page)
    page.write_text("## Reading the plot\n- x is 2θ\n\n## Settings\n", encoding="utf-8")
    assert reading_the_plot_missing(page) is None
    assert reading_the_plot_missing(tmp_path / "gone.md")


_DRIVER = """
import json
from labpipe import automation
from labpipe.safety import load_pipeline_config
config = load_pipeline_config()
before = automation._statuses(config)
done = automation._analyse_code_only(config, "test")
state = automation._notify_news(config, before)
again = automation._notify_news(config, state)      # nothing is new the second time
print(json.dumps({"done": done}))
"""


def _code_only_pass(env) -> list[str]:
    completed = subprocess.run([sys.executable, "-c", _DRIVER], env=env, capture_output=True,
                               text=True, timeout=600)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)["done"]


def _told(project) -> list[dict]:
    path = project / "logs" / "notifications.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] \
        if path.exists() else []


def test_code_only_analysis_runs_edited_lab_code_and_tells_review_flags_once(tmp_path):
    project, raw, env, example, _ = _scaffold(tmp_path)
    registry = project / "lab/methods.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace(
        "subagent: demo-analyst", "subagent: null"), encoding="utf-8")
    _labpipe(env, "scan")
    [eid] = [e["entry_id"] for e in _labpipe(env, "pending") if e["method"] == "demo"]
    assert _code_only_pass(env) == [eid]
    out = project / _labpipe(env, "status", eid)["output_dir"]
    first = json.loads((out / "provenance.json").read_text(encoding="utf-8"))["code"]
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]["n_points"] == 20
    assert not [n for n in _told(project) if n["kind"] == "to_check"]

    analyze = project / "lab/methods/demo/analyze.py"       # an edit, after a first run
    analyze.write_text(analyze.read_text(encoding="utf-8").replace(
        '"n_points": int(x.size)', '"n_points": 12345').replace(
        "if not np.all(np.isfinite(data)):", "if True:"), encoding="utf-8")
    assert _labpipe(env, "scan")["counts"]["stale"] == 1
    _labpipe(env, "requeue", "--stale")
    assert _code_only_pass(env) == [eid]
    out = project / _labpipe(env, "status", eid)["output_dir"]
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    second = json.loads((out / "provenance.json").read_text(encoding="utf-8"))["code"]
    assert results["summary"]["n_points"] == 12345          # the edited code ran
    assert second["analysis_version"] != first["analysis_version"]
    assert second["analysis_version"] == _labpipe(env, "methods", "versions")["demo"]["analysis_version"]
    flagged = [n for n in _told(project) if n["kind"] == "to_check"]
    assert len(flagged) == 1 and flagged[0]["needs_you"]    # once, although checked twice
    assert "1 DEMO measurement(s) to check: D-1_a (non_finite_values)" in flagged[0]["message"]
    assert "review queue" in flagged[0]["message"] or "review_queue.md" in flagged[0]["message"]
    queue = (project / "logs" / "review_queue.md").read_text(encoding="utf-8")
    assert "checks) (1)" in queue and "non_finite_values" in queue


def test_a_code_only_process_that_dies_is_recorded_as_failed_with_its_stderr(tmp_path):
    project, raw, env, example, _ = _scaffold(tmp_path)
    registry = project / "lab/methods.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace(
        "subagent: demo-analyst", "subagent: null"), encoding="utf-8")
    _labpipe(env, "scan")
    (project / "lab/methods/demo/analyze.py").write_text("raise SystemExit('boom in method')\n",
                                                         encoding="utf-8")
    [eid] = [e["entry_id"] for e in _labpipe(env, "pending") if e["method"] == "demo"]
    assert _code_only_pass(env) == []
    status = _labpipe(env, "status", eid)
    assert status["status"] == "failed" and "boom in method" in status["status_reason"]


def test_the_watcher_restarts_when_lab_methods_change(tmp_path, monkeypatch):
    from labpipe import automation
    monkeypatch.setattr(labmethods.safety, "PROJECT_ROOT", tmp_path)
    (tmp_path / "lab/methods/demo/__pycache__").mkdir(parents=True)
    method = tmp_path / "lab/methods/demo/analyze.py"
    method.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "lab/methods.yaml").write_text("methods: {}\n", encoding="utf-8")
    start = labmethods.source_hash()
    (tmp_path / "lab/methods/demo/__pycache__/a.pyc").write_bytes(b"cache")
    assert labmethods.source_hash() == start                 # caches do not count
    events = []
    monkeypatch.setattr(automation, "in_container", lambda: True)
    monkeypatch.setattr(automation, "_start_page", lambda config: None)
    monkeypatch.setattr(automation, "run", lambda config, reason="": {"status": "nothing_to_do"})
    monkeypatch.setattr(automation, "_snapshot", lambda config: {})
    monkeypatch.setattr(automation, "_last_nightly", lambda config: None)
    monkeypatch.setattr(automation, "_log", lambda config, event: events.append(event))
    monkeypatch.setattr(automation.time, "sleep",
                        lambda seconds: method.write_text("x = 2\n", encoding="utf-8"))
    config = {"settle_minutes": 3, "logs_dir": "logs", "automation": {}}
    with pytest.raises(SystemExit) as stopped:
        automation.watch(config)
    assert stopped.value.code == 3 and labmethods.source_hash() != start
    assert {"event": "watch_restart", "reason": "lab methods changed"} in events
    method.write_text("x = 1\n", encoding="utf-8")
    assert labmethods.source_hash() == start
    (tmp_path / "lab/methods.yaml").write_text("methods: {a: {}}\n", encoding="utf-8")
    assert labmethods.source_hash() != start                 # the registry counts too
