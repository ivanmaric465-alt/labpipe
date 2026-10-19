"""Text files are read and written as UTF-8 everywhere (Windows defaults to another code page,
so a missing `encoding=` breaks there on Å, θ, → …)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_text_read_and_write_names_its_encoding():
    missing = []
    for path in [*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("tests").glob("*.py"),
                 *ROOT.joinpath("scripts").glob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("read_text", "write_text")):
                continue
            positional = 1 if node.func.attr == "read_text" else 2   # encoding as an argument
            if len(node.args) < positional and not any(k.arg == "encoding" for k in node.keywords):
                missing.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert missing == [], f"read_text/write_text without encoding=\"utf-8\": {missing}"
