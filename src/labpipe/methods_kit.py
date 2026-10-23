"""`labpipe methods new | check`: scaffold a new analysis method and check its contract.

`new NAME --example FILE` creates, from src/labpipe/methods/template/, and never overwrites:
  in a project (the lab's own method, docs/ADDING_A_METHOD.md):
    lab/methods/NAME/  (analyze.py with METHOD set, __init__.py, config.yaml, README.md = the
    design note), lab/agents/NAME-analyst.md and an entry in lab/methods.yaml
  in the labpipe repository (dev mode, a method for labpipe itself):
    src/labpipe/methods/NAME/  (analyze.py with METHOD set, __init__.py),
    config/NAME.yaml, agents/NAME-analyst.md, tests/test_NAME.py,
    docs/methods/NAME.md (design note, if missing) and a methods.yaml entry
  (patterns from the example's extension).

`import NAME --folder F --files "*.png,*.csv" --description D` takes your own analyses (made
outside labpipe) into the report, in a project only: it writes lab/methods/NAME/README.md (the
description form) and config.yaml, lab/agents/NAME-analyst.md and an entry `kind: imported` in
lab/methods.yaml, never overwriting; no Python (labpipe.methods.imported does the work).

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

from labpipe import labmethods, safety
from labpipe.naming import measurement_id
from labpipe.provenance import sha256_file, utc_now
from labpipe.registry import classify, load_methods
from labpipe.safety import CODE_ROOT, PROJECT_ROOT, atomic_write_text, is_dev

TEMPLATE = CODE_ROOT / "src" / "labpipe" / "methods" / "template"
NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
REQUIRED_OUTPUTS = ("results.json", "plot.png", "plot.pdf", "provenance.json")
SEVERITIES = {"info", "warning", "review"}


class MethodKitError(RuntimeError):
    pass


def _fill(text: str, name: str, description: str, extension: str) -> str:
    return (text.replace("{{method}}", name).replace("{{description}}", description)
            .replace("{{extension}}", extension))


def _entry(name: str, description: str, extension: str, agent: str | None, *, lab: bool) -> str:
    package = f"labpipe_lab.{name}" if lab else f"labpipe.methods.{name}"
    return (f"\n  {name}:\n"
            f"    description: {json.dumps(description, ensure_ascii=False)}\n"
            f'    patterns: ["**/*{extension}"]      # narrow this if other methods use {extension}\n'
            f"    ignore: []\n"
            f"    # recognise: {package}.analyze:recognise   # optional content check\n"
            f"    # companions: {package}.inputs:companions   # optional: other files it reads\n"
            + (f"    subagent: {agent}\n" if agent else
               "    subagent: null                     # analysed by code alone\n"
               "    notes: false\n")
            + f'    cli: "labpipe {name} analyze --entry {{entry_id}}"\n'
            f"    module: {package}.analyze\n"
            f"    config: {'lab/methods/' + name + '/config.yaml' if lab else 'config/' + name + '.yaml'}\n"
            f"    outputs: [results.json, plot.png, plot.pdf, provenance.json, notes.md]\n")


def _analyze_text(name: str, description: str, page: str, package: str) -> str:
    return ((TEMPLATE / "analyze.py").read_text(encoding="utf-8")
            .replace('METHOD = "template"', f'METHOD = "{name}"')
            .replace('"labpipe.template.results/1"', f'"labpipe.{name}.results/1"')
            .replace("labpipe.methods.template.analyze.run", f"{package}.analyze.run")
            .replace('"""Template method: a minimal, complete example of the method contract.',
                     f'"""{description}: method `{name}` (design: {page}).\n\n'
                     "Scaffolded from the template; replace the reading and numerics sections.\n"
                     "Original template text follows."))


def new(name: str, example: Path, description: str, analyst: bool = True) -> dict[str, Any]:
    """In a project: the lab's own method in lab/. In the repository (dev mode): a shipped one."""
    if not NAME.match(name) or name in ("template", "methods") or name in labmethods.RESERVED:
        raise MethodKitError(f"method name must be lower-case letters/digits/_ "
                             f"(e.g. sem, echem), not {name!r}")
    if safety.NO_PROJECT:
        raise MethodKitError(safety.NO_PROJECT_MESSAGE)
    if name in load_methods():
        raise MethodKitError(f"{name} is already in methods.yaml or lab/methods.yaml")
    extension = example.suffix.lower()
    if not extension:
        raise MethodKitError("the example file needs an extension (it becomes the pattern)")
    lab = not is_dev(PROJECT_ROOT)
    agent = f"{name}-analyst" if analyst else None
    base = PROJECT_ROOT if lab else CODE_ROOT
    template = lambda file: (TEMPLATE / file).read_text(encoding="utf-8")  # noqa: E731
    if lab:
        folder = PROJECT_ROOT / "lab" / "methods" / name
        page = f"lab/methods/{name}/README.md"
        targets = {
            folder / "__init__.py": f'"""{description}"""\n',
            folder / "analyze.py": _analyze_text(name, description, page, f"labpipe_lab.{name}"),
            folder / "config.yaml": template("config.example.yaml").replace(
                "labpipe.template.config/1", f"labpipe.{name}.config/1").replace(
                "Copy to config/<method>.yaml.", f"The defaults of {name}.").replace(
                "(config/samples.yaml)", "(config/samples.yaml), or for all samples in "
                f"config/lab.yaml → {name}"),
            folder / "README.md": _fill(template("design.md"), name, description, extension)
                .replace(f"`config/{name}.yaml`", f"`lab/methods/{name}/config.yaml`"),
        }
        if agent:
            targets[PROJECT_ROOT / "lab" / "agents" / f"{agent}.md"] = _fill(
                template("analyst.md"), name, description, extension).replace(
                f"docs/methods/{name}.md", page)
    else:
        package = CODE_ROOT / "src" / "labpipe" / "methods" / name
        page = f"docs/methods/{name}.md"
        targets = {
            package / "__init__.py": f'"""{description}"""\n',
            package / "analyze.py": _analyze_text(name, description, page, f"labpipe.methods.{name}"),
            CODE_ROOT / "config" / f"{name}.yaml": template("config.example.yaml").replace(
                "labpipe.template.config/1", f"labpipe.{name}.config/1"),
            CODE_ROOT / "tests" / f"test_{name}.py": _fill(template("test_method.py.txt"), name,
                                                           description, extension),
        }
        if agent:
            targets[CODE_ROOT / "agents" / f"{agent}.md"] = _fill(
                template("analyst.md"), name, description, extension)
        design = CODE_ROOT / "docs" / "methods" / f"{name}.md"
        if not design.exists():
            targets[design] = _fill(template("design.md"), name, description, extension)
    existing = [p.relative_to(base).as_posix() for p in targets if p.exists()]
    if existing:
        raise MethodKitError(f"would overwrite {existing}; nothing created")
    for path, text in targets.items():
        atomic_write_text(path, text, pipeline=False)
    registry = base / ("lab/methods.yaml" if lab else "methods.yaml")
    entry = _entry(name, description, extension, agent, lab=lab)
    if registry.exists():
        text = registry.read_text(encoding="utf-8").rstrip() + "\n"
        if lab and "methods:" not in text:
            text += "\nmethods:\n"
    else:
        text = ("# The lab's own methods (docs/ADDING_A_METHOD.md). Same entries as labpipe's "
                "methods.yaml;\n# modules are labpipe_lab.<name>, the package lab/methods/<name>/.\n"
                "schema: labpipe.methods/1\n\nmethods:\n")
    atomic_write_text(registry, text + entry, pipeline=False)
    where = "lab/" if lab else ""
    return {"created": [p.relative_to(base).as_posix() for p in targets]
            + [f"{'lab/' if lab else ''}methods.yaml (entry)"],
            "mode": "project (the lab's own method)" if lab else "dev (a method of labpipe)",
            "next": [f"agree the design with the user in {page} first",
                     f"implement reading and numerics in "
                     f"{'lab/methods/' + name if lab else 'src/labpipe/methods/' + name}/analyze.py",
                     f"put every tunable number in "
                     f"{'lab/methods/' + name + '/config.yaml' if lab else 'config/' + name + '.yaml'}",
                     *([] if lab else [f"write the synthetic test in tests/test_{name}.py"]),
                     *([f"adapt {where}agents/{agent}.md (what to look at, what the notes say)"]
                       if agent else []),
                     f"labpipe methods check {name} --example <file>  (until it passes)",
                     "labpipe project sync; labpipe scan" if lab else "labpipe agents sync; labpipe scan"]}


def _folder(folder: str) -> str:
    cleaned = folder.replace("\\", "/").strip().strip("/")
    if not cleaned or cleaned == "." or ".." in cleaned.split("/") or ":" in cleaned.split("/")[0]:
        raise MethodKitError(f"--folder is a folder of your data folder, e.g. \"Raman/fits\" "
                             f"(not {folder!r}): the files in it and below are the ones taken in")
    return cleaned


def _globs(files: str) -> list[str]:
    globs = [g.strip() for g in files.split(",") if g.strip()]
    if not globs or any("/" in g or "\\" in g or ".." in g for g in globs):
        raise MethodKitError(f"--files is a comma-separated list of file names with * in them, "
                             f"e.g. \"*.png,*.csv,*.md\" (no folders), not {files!r}")
    return globs


def import_analysis(name: str, folder: str, files: str, description: str, *,
                    sample_from: str = "file", analyst: bool = True,
                    raw_roots: list[dict] | None = None) -> dict[str, Any]:
    """Take your own analyses into the project (docs/methods/imported.md): the entry, the
    description page, the settings file and the analyst's role. Never overwrites."""
    if not NAME.match(name) or name in ("template", "methods") or name in labmethods.RESERVED:
        raise MethodKitError(f"the name must be lower-case letters/digits/_ "
                             f"(e.g. raman_fits), not {name!r}")
    if safety.NO_PROJECT:
        raise MethodKitError(safety.NO_PROJECT_MESSAGE)
    if is_dev(PROJECT_ROOT):
        raise MethodKitError("importing your own analyses is a project thing: run it in a "
                             "project (`labpipe project new`), not in the labpipe repository")
    if sample_from not in labmethods.SAMPLE_FROM:
        raise MethodKitError("--sample-from is 'file' (the sample ID is in the file name) or "
                             "'folder' (it is the name of the folder)")
    if name in load_methods():
        raise MethodKitError(f"{name} is already in methods.yaml or lab/methods.yaml")
    folder, globs = _folder(folder), _globs(files)
    template = lambda file: (TEMPLATE / file).read_text(encoding="utf-8")  # noqa: E731
    fill = lambda text: (_fill(text, name, description, "").replace(  # noqa: E731
        "{{folder}}", folder).replace("{{files}}", ", ".join(f"`{g}`" for g in globs)).replace(
        "{{sample_from}}", "the folder's name" if sample_from == "folder" else
        "the start of the file name"))
    folder_dir = PROJECT_ROOT / "lab" / "methods" / name
    agent = f"{name}-analyst"
    targets = {folder_dir / "README.md": fill(template("imported-readme.md")),
               folder_dir / "config.yaml": fill(template("imported-config.yaml"))}
    if analyst:
        targets[PROJECT_ROOT / "lab" / "agents" / f"{agent}.md"] = fill(
            template("imported-analyst.md"))
    existing = [p.relative_to(PROJECT_ROOT).as_posix() for p in targets if p.exists()]
    if existing:
        raise MethodKitError(f"would overwrite {existing}; nothing created")
    for path, text in targets.items():
        atomic_write_text(path, text, pipeline=False)
    registry = PROJECT_ROOT / "lab" / "methods.yaml"
    entry = (f"\n  {name}:\n    kind: imported\n"
             f"    description: {json.dumps(description, ensure_ascii=False)}\n"
             f"    patterns: {json.dumps([f'{folder}/**/{g}' for g in globs], ensure_ascii=False)}\n"
             f"    sample_from: {sample_from}"
             + ("            # folder: the sample ID is the name of the folder of the file"
                if sample_from == "folder" else "") + "\n"
             + ("" if analyst else "    subagent: null                     # no analyst agent\n"
                "    notes: false                       # only the files in the report\n"))
    if registry.exists():
        text = registry.read_text(encoding="utf-8").rstrip() + "\n"
        if "methods:" not in text:
            text += "\nmethods:\n"
    else:
        text = ("# The lab's own methods (docs/ADDING_A_METHOD.md). Same entries as labpipe's "
                "methods.yaml;\n# modules are labpipe_lab.<name>, the package lab/methods/<name>/; "
                "`kind: imported` entries\n# are your own analyses made outside labpipe "
                "(docs/methods/imported.md).\nschema: labpipe.methods/1\n\nmethods:\n")
    atomic_write_text(registry, text + entry, pipeline=False)
    page = f"lab/methods/{name}/README.md"
    missing = [] if not raw_roots else ([] if any((Path(r["path"]) / folder).is_dir()
                                                  for r in raw_roots) else [
        f"no folder {folder!r} in your data folder(s) yet (patterns match when it exists)"])
    return {"created": [p.relative_to(PROJECT_ROOT).as_posix() for p in targets]
            + ["lab/methods.yaml (entry)"], "patterns": [f"{folder}/**/{g}" for g in globs],
            **({"warnings": missing} if missing else {}),
            "next": [f"fill in the description in {page} (the first paragraph is shown in the "
                     "report; your agent can write it from what you tell it)",
                     "labpipe project sync   (so the agents see the new role)",
                     "labpipe scan", "labpipe analyze --pending"]}


def reading_the_plot_missing(page: Path) -> str | None:
    """Why the method page has no filled-in "Reading the plot" section (None when it has one):
    the section is absent, or has no bullet with text (the template's HTML comment and an
    empty `-` do not count)."""
    if not page.is_file():
        return f"{page.name} not found"
    text = re.sub(r"<!--.*?-->", "", page.read_text(encoding="utf-8"), flags=re.S)
    match = re.search(r"^## Reading the plot[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match:
        return "no \"Reading the plot\" section"
    if not re.search(r"^[-*] +\S", match.group(1), re.M):
        return "\"Reading the plot\" has no bullets yet"
    return None


def _check(results: list, name: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def _run(module, example: Path, out: Path, pipeline: bool, settings=None,
         sources=None, method: str | None = None) -> dict[str, Any]:
    return module.run(example, out, settings=settings, settings_sources=sources, context={
        "sample_id": None, "measurement_id": measurement_id(example.name),
        "relative_path": example.name, "method": method}, pipeline=pipeline)


def check(name: str, example: Path, config: dict | None = None,
          work_dir: Path | None = None) -> dict[str, Any]:
    """Outputs go to logs/method-check/ (pipeline write rules) or, for tests, work_dir."""
    results: list[dict[str, Any]] = []
    spec = load_methods().get(name)
    if not _check(results, "registry entry", spec is not None, "methods.yaml → methods." + name):
        return {"method": name, "ok": False, "checks": results}
    if spec.get("kind") == "imported":
        raise MethodKitError(f"{name} is an imported analysis (made outside labpipe): there is "
                             "no method code to check; `labpipe replay` checks its files")
    for key in ("patterns", "module", "config"):
        _check(results, f"registry: {key}", spec.get(key), str(spec.get(key)))
    lab = bool(spec.get("lab"))
    _check(results, "settings file", spec.get("config") and
           labmethods.defaults_file(spec).exists(), str(spec.get("config")))
    if spec.get("subagent"):
        base = PROJECT_ROOT if lab else CODE_ROOT
        role = base / ("lab/agents" if lab else "agents") / f"{spec['subagent']}.md"
        _check(results, "analyst role file", role.exists() and "labpipe notes verify" in
               role.read_text(encoding="utf-8"), f"{role.relative_to(base).as_posix()} "
               "must exist and keep the `labpipe notes verify` step")
    else:
        _check(results, "no analyst: notes switched off", spec.get("notes") is False,
               "a method without `subagent` needs `notes: false` in methods.yaml")
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
        first = _run(module, example, work / "run1", pipeline, method=name)
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
                  sources=record["parameters"].get("sources"), method=name)
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
    page = (PROJECT_ROOT / "lab" / "methods" / name / "README.md" if lab
            else CODE_ROOT / "docs" / "methods" / f"{name}.md")
    warnings = []
    if reason := reading_the_plot_missing(page):     # a warning: the method works without it
        warnings.append(f"{reason} in {page.name}: write 3-5 bullets on how to read the figure "
                        "(axes, lines, colours, good vs look again); agents explain the plot "
                        "from it")
    return {"method": name, "ok": all(r["ok"] for r in results), "checks": results,
            **({"warnings": warnings} if warnings else {}), "work_dir": str(work)}
