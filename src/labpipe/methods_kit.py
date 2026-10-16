"""`labpipe methods new | check`: scaffold a new analysis method and check its contract.

`new NAME --example FILE` creates, from src/labpipe/methods/template/:
  src/labpipe/methods/NAME/  (analyze.py with METHOD set, __init__.py)
  config/NAME.yaml, agents/NAME-analyst.md, tests/test_NAME.py,
  docs/methods/NAME.md (design note, if missing) and a methods.yaml entry
  (patterns from the example's extension). It never overwrites anything.

`check NAME --example FILE` runs the method on the example (outputs under
logs/method-check/, the example is only read) and verifies the contract that
the pipeline, replay and the agents rely on: registry entry, recognition,
required outputs, results.json shape, provenance hashes, determinism (a second
run from the recorded settings gives identical files), raw file untouched.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
from pathlib import Path
from typing import Any

from labpipe.naming import measurement_id
from labpipe.provenance import sha256_file, utc_now
from labpipe.registry import classify, load_methods
from labpipe.safety import PROJECT_ROOT, atomic_write_text

TEMPLATE = PROJECT_ROOT / "src" / "labpipe" / "methods" / "template"
NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
REQUIRED_OUTPUTS = ("results.json", "plot.png", "plot.pdf", "provenance.json")
SEVERITIES = {"info", "warning", "review"}


class MethodKitError(RuntimeError):
    pass


def _fill(text: str, name: str, description: str, extension: str) -> str:
    return (text.replace("{{method}}", name).replace("{{description}}", description)
            .replace("{{extension}}", extension))


def new(name: str, example: Path, description: str) -> dict[str, Any]:
    if not NAME.match(name) or name in ("template", "methods"):
        raise MethodKitError(f"method name must be lower-case letters/digits/_ "
                             f"(e.g. sem, echem), not {name!r}")
    if name in load_methods():
        raise MethodKitError(f"{name} is already in methods.yaml")
    extension = example.suffix.lower()
    if not extension:
        raise MethodKitError("the example file needs an extension (it becomes the pattern)")
    package = PROJECT_ROOT / "src" / "labpipe" / "methods" / name
    analyst = f"{name}-analyst"
    targets = {
        package / "__init__.py": f'"""{description}"""\n',
        package / "analyze.py": (TEMPLATE / "analyze.py").read_text(encoding="utf-8")
            .replace('METHOD = "template"', f'METHOD = "{name}"')
            .replace('"labpipe.template.results/1"', f'"labpipe.{name}.results/1"')
            .replace("labpipe.methods.template.analyze.run", f"labpipe.methods.{name}.analyze.run")
            .replace('"""Template method: a minimal, complete example of the method contract.',
                     f'"""{description}: method `{name}` (design: docs/methods/{name}.md).\n\n'
                     "Scaffolded from the template; replace the reading and numerics sections.\n"
                     "Original template text follows."),
        PROJECT_ROOT / "config" / f"{name}.yaml": (TEMPLATE / "config.example.yaml")
            .read_text(encoding="utf-8").replace("labpipe.template.config/1",
                                                 f"labpipe.{name}.config/1"),
        PROJECT_ROOT / "agents" / f"{analyst}.md": _fill(
            (TEMPLATE / "analyst.md").read_text(encoding="utf-8"), name, description, extension),
        PROJECT_ROOT / "tests" / f"test_{name}.py": _fill(
            (TEMPLATE / "test_method.py.txt").read_text(encoding="utf-8"), name, description,
            extension),
    }
    design = PROJECT_ROOT / "docs" / "methods" / f"{name}.md"
    if not design.exists():
        targets[design] = _fill((TEMPLATE / "design.md").read_text(encoding="utf-8"), name,
                                description, extension)
    existing = [p.relative_to(PROJECT_ROOT).as_posix() for p in targets if p.exists()]
    if existing:
        raise MethodKitError(f"would overwrite {existing}; nothing created")
    for path, text in targets.items():
        atomic_write_text(path, text, pipeline=False)
    entry = (f"\n  {name}:\n"
             f"    description: {json.dumps(description, ensure_ascii=False)}\n"
             f'    patterns: ["**/*{extension}"]      # narrow this if other methods use {extension}\n'
             f"    ignore: []\n"
             f"    # recognise: labpipe.methods.{name}.analyze:recognise   # optional content check\n"
             f"    subagent: {analyst}\n"
             f'    cli: "labpipe {name} analyze --entry {{entry_id}}"\n'
             f"    module: labpipe.methods.{name}.analyze\n"
             f"    config: config/{name}.yaml\n"
             f"    outputs: [results.json, plot.png, plot.pdf, provenance.json, notes.md]\n")
    registry = PROJECT_ROOT / "methods.yaml"
    atomic_write_text(registry, registry.read_text(encoding="utf-8").rstrip() + "\n" + entry,
                      pipeline=False)
    return {"created": [p.relative_to(PROJECT_ROOT).as_posix() for p in targets] + ["methods.yaml (entry)"],
            "next": [f"agree the design with the user in docs/methods/{name}.md first",
                     f"implement reading and numerics in src/labpipe/methods/{name}/analyze.py",
                     f"put every tunable number in config/{name}.yaml",
                     f"write the synthetic test in tests/test_{name}.py",
                     f"adapt agents/{analyst}.md (what to look at, what the notes say)",
                     f"labpipe methods check {name} --example <file>  (until it passes)",
                     "labpipe agents sync; labpipe scan"]}


def _check(results: list, name: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def _run(module, example: Path, out: Path, pipeline: bool, settings=None,
         sources=None) -> dict[str, Any]:
    return module.run(example, out, settings=settings, settings_sources=sources, context={
        "sample_id": None, "measurement_id": measurement_id(example.name),
        "relative_path": example.name}, pipeline=pipeline)


def check(name: str, example: Path, config: dict | None = None,
          work_dir: Path | None = None) -> dict[str, Any]:
    """Outputs go to logs/method-check/ (pipeline write rules) or, for tests, work_dir."""
    results: list[dict[str, Any]] = []
    spec = load_methods().get(name)
    if not _check(results, "registry entry", spec is not None, "methods.yaml → methods." + name):
        return {"method": name, "ok": False, "checks": results}
    for key in ("patterns", "module", "config", "subagent"):
        _check(results, f"registry: {key}", spec.get(key), str(spec.get(key)))
    _check(results, "settings file", (PROJECT_ROOT / str(spec.get("config"))).exists(),
           str(spec.get("config")))
    role = PROJECT_ROOT / "agents" / f"{spec.get('subagent')}.md"
    _check(results, "analyst role file", role.exists() and "labpipe notes verify" in
           role.read_text(encoding="utf-8"), f"{role.relative_to(PROJECT_ROOT).as_posix()} must exist and "
           "keep the `labpipe notes verify` step")
    try:
        module = importlib.import_module(spec["module"])
    except Exception as exc:  # report, do not crash the check
        _check(results, "module imports", False, f"{type(exc).__name__}: {exc}")
        return {"method": name, "ok": False, "checks": results}
    parameters = inspect.signature(module.run).parameters
    _check(results, "run() signature", all(p in parameters for p in (
        "settings", "settings_sources", "context", "pipeline")),
           "run(input_path, out_dir, *, settings, settings_sources, context, pipeline)")
    _check(results, "AnalysisError defined", hasattr(module, "AnalysisError"))

    example = example.resolve()
    if not _check(results, "example file exists", example.is_file(), str(example)):
        return {"method": name, "ok": False, "checks": results}
    claimed, kind = classify(example.name, {name: spec}, root=example.parent)
    _check(results, "example is recognised as this method", claimed == name and kind == "analyse",
           f"classified as {claimed!r}/{kind!r} by patterns {spec.get('patterns')}")
    before = (sha256_file(example), example.stat().st_mtime_ns,
              sorted(p.name for p in example.parent.iterdir()))

    pipeline = work_dir is None
    work = (PROJECT_ROOT / config["logs_dir"] / "method-check"
            / f"{name}-{utc_now().replace(':', '')}") if pipeline else Path(work_dir)
    try:
        first = _run(module, example, work / "run1", pipeline)
    except Exception as exc:
        _check(results, "run on the example", False, f"{type(exc).__name__}: {exc}")
        return {"method": name, "ok": False, "checks": results, "work_dir": str(work)}
    _check(results, "run on the example", True)
    _check(results, "run() return value", isinstance(first, dict) and all(
        k in first for k in ("status", "out_dir", "checks", "outputs", "settings_sha256")),
           "keys status, out_dir, checks, outputs, settings_sha256")
    out = work / "run1"
    missing = [n for n in REQUIRED_OUTPUTS if not (out / n).is_file()]
    _check(results, "required outputs", not missing, f"missing: {missing}" if missing else "")
    if missing:
        return {"method": name, "ok": False, "checks": results, "work_dir": str(work)}

    data = json.loads((out / "results.json").read_text(encoding="utf-8"),
                      parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    _check(results, "results: schema", str(data.get("schema", "")).startswith(f"labpipe.{name}."),
           f"schema {data.get('schema')!r} should be labpipe.{name}.results/N")
    _check(results, "results: status", data.get("status") in ("ok", "review"),
           f"status {data.get('status')!r}")
    _check(results, "results: units", isinstance(data.get("units"), dict) and data["units"],
           "a units dictionary for every quantity")
    checks = data.get("checks")
    bad = [c for c in checks or [] if not (isinstance(c, dict) and c.get("code") and
                                          c.get("severity") in SEVERITIES and "message" in c)]
    _check(results, "results: checks", isinstance(checks, list) and not bad,
           f"each check needs code, severity in {sorted(SEVERITIES)}, message" +
           (f"; bad: {bad[:2]}" if bad else ""))
    _check(results, "results: review status matches checks",
           (data.get("status") == "review") == any(c.get("severity") == "review"
                                                    for c in checks or []))

    record = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    _check(results, "provenance: input hash", record.get("input", {}).get("sha256") == before[0])
    _check(results, "provenance: resolved settings",
           isinstance(record.get("parameters", {}).get("resolved"), dict))
    wrong = [n for n, h in (record.get("outputs") or {}).items()
             if not (out / n).is_file() or sha256_file(out / n) != h]
    _check(results, "provenance: output hashes", record.get("outputs") and not wrong,
           f"differ: {wrong}" if wrong else "")

    second = _run(module, example, work / "run2", pipeline,
                  settings=record["parameters"]["resolved"],
                  sources=record["parameters"].get("sources"))
    differ = [n for n in ("results.json", "plot.png", "plot.pdf")
              if sha256_file(work / "run2" / n) != sha256_file(out / n)]
    _check(results, "deterministic and regenerable from provenance", not differ,
           f"second run (recorded settings) differs in {differ}" if differ else "")
    _check(results, "summary from run() matches files", second.get("outputs") == first.get("outputs"))

    if spec.get("summary"):
        try:
            summary = importlib.import_module(spec["summary"]).summarize(data)
            _check(results, "summary module", isinstance(summary, dict))
        except Exception as exc:
            _check(results, "summary module", False, f"{type(exc).__name__}: {exc}")

    after = (sha256_file(example), example.stat().st_mtime_ns,
             sorted(p.name for p in example.parent.iterdir()))
    _check(results, "raw file and its folder untouched", after == before)
    stray = sorted({p.name for p in work.iterdir()} - {"run1", "run2"})
    _check(results, "writes only into the output folder", not stray, f"unexpected: {stray}")
    return {"method": name, "ok": all(r["ok"] for r in results), "checks": results,
            "work_dir": str(work)}
