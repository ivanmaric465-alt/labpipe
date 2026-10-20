"""The docs agree with the code where that can be checked mechanically (docs/docmap.yaml has
the rest: which docs to review when code changes, scripts/check_docs.py enforces it)."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest
import yaml

from labpipe import cli
from labpipe.registry import load_methods

ROOT = Path(__file__).resolve().parents[1]
INTERNAL_COMMANDS = {"guard"}          # run by the agent tools' hooks, not by people or agents


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _files() -> list[str]:
    return [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]


def _commands() -> set[str]:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return set(sub.choices) - set(load_methods())


def test_every_command_is_documented_and_every_documented_command_exists():
    docs = _text("AGENTS.md") + _text("README.md") + _text("docs/SETUP.md")
    undocumented = sorted(c for c in _commands() - INTERNAL_COMMANDS
                          if not re.search(rf"labpipe {re.escape(c)}\b", docs))
    assert undocumented == [], f"commands not in AGENTS.md, README.md or docs/SETUP.md: {undocumented}"
    table = re.findall(r"^\| `labpipe ([a-z][a-z-]*)", _text("AGENTS.md"), re.M)
    ghosts = sorted(set(table) - _commands())
    assert ghosts == [], f"AGENTS.md lists commands that do not exist: {ghosts}"


def test_architecture_lists_every_module_and_no_others():
    text = _text("docs/ARCHITECTURE.md")
    listed = set(re.findall(r"`((?:plotting/|methods/<m>/)?[a-z_]+\.py)`", text))
    modules = {p.name for p in (ROOT / "src" / "labpipe").glob("*.py") if p.name != "__init__.py"}
    modules |= {f"plotting/{p.name}" for p in (ROOT / "src" / "labpipe" / "plotting").glob("*.py")
                if p.name != "__init__.py"}
    missing = sorted(m for m in modules if m not in listed)
    assert missing == [], f"modules missing from docs/ARCHITECTURE.md → Module map: {missing}"
    ghosts = sorted(m for m in listed if not m.startswith("methods/") and m not in modules)
    assert ghosts == [], f"docs/ARCHITECTURE.md lists modules that do not exist: {ghosts}"


def test_docmap_paths_exist_and_cover_every_module():
    docmap = yaml.safe_load(_text("docs/docmap.yaml"))
    files = _files()
    for name, area in docmap["areas"].items():
        for pattern in area["code"] + area["docs"]:
            assert any(PurePosixPath(f).full_match(pattern) for f in files), \
                f"docs/docmap.yaml [{name}]: nothing matches {pattern}"
    patterns = [p for area in docmap["areas"].values() if area.get("on") != "added or removed"
                for p in area["code"]]
    uncovered = sorted(f for f in files if f.startswith("src/labpipe/") and f.endswith(".py")
                       and not any(PurePosixPath(f).full_match(p) for p in patterns))
    assert uncovered == [], f"modules in no docs/docmap.yaml area: {uncovered}"


def test_relative_links_in_markdown_resolve():
    broken = []
    for path in (p for p in _files() if p.endswith(".md")):
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", _text(path)):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (ROOT / path).parent.joinpath(target).exists():
                broken.append(f"{path} → {target}")
    assert broken == [], f"broken links: {broken}"


def test_settings_index_lists_every_settings_page():
    index = _text("docs/settings/README.md")
    pages = sorted(p.name for p in (ROOT / "docs" / "settings").glob("*.md") if p.name != "README.md")
    missing = [p for p in pages if f"]({p})" not in index]
    assert missing == [], f"docs/settings/README.md does not link: {missing}"


@pytest.mark.parametrize("defaults, page", [("config/plots.yaml", "docs/settings/plots.md"),
                                            ("config/report.yaml", "docs/settings/report.md")])
def test_every_default_key_is_on_its_settings_page(defaults, page):
    keys = yaml.safe_load(_text(defaults))
    missing = sorted(k for k in keys if f"{k}:" not in _text(page))
    assert missing == [], f"{page} does not describe: {missing}"


def test_every_method_has_its_page_and_its_plot_keys_are_documented():
    plots = _text("docs/settings/plots.md")
    for method, spec in load_methods().items():
        page = ROOT / "docs" / "methods" / f"{method}.md"
        assert page.exists(), f"no docs/methods/{method}.md"
        headings = set(re.findall(r"^## (\w+)", page.read_text(encoding="utf-8"), re.M))
        assert {"Settings", "Quantities"} <= headings, f"docs/methods/{method}.md: missing headings"
        plot = (yaml.safe_load(_text(spec["config"])) or {}).get("plot") or {}
        missing = sorted(k for k in plot if k not in plots)
        assert missing == [], f"docs/settings/plots.md does not describe {method} plot keys {missing}"


def test_every_agent_role_is_in_agents_md():
    agents_md = _text("AGENTS.md")
    for path in (ROOT / "agents").glob("*.md"):
        role = path.stem
        named = role in agents_md or (role.endswith("-analyst") and "<method>-analyst" in agents_md)
        assert named, f"AGENTS.md → Agents does not mention the role {role}"


@pytest.mark.parametrize("method", sorted(m for m, s in load_methods().items() if not s.get("lab")))
def test_every_shipped_method_page_has_reading_the_plot(method):
    text = re.sub(r"<!--.*?-->", "", _text(f"docs/methods/{method}.md"), flags=re.S)
    section = re.search(r"^## Reading the plot\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert section, f"docs/methods/{method}.md has no 'Reading the plot' section"
    bullets = re.findall(r"^- +\S", section.group(1), re.M)
    assert 3 <= len(bullets) <= 5, f"'Reading the plot' of {method} has {len(bullets)} bullets (3-5)"
