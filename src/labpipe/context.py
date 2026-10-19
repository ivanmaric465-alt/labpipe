"""The project's context folder: `labpipe context init | index`.

    context/
      context.md        the project in plain words (you write it; templates/context/)
      instructions.md   how the postdoc uses context.md and the documents (you edit it)
      documents/        your files: work plan, literature (PDF, DOCX, Markdown, text); read-only for agents
      .text/            plain-text copies of documents/ with page markers, and index.json

`init` creates what is missing and never overwrites. `index` extracts the text of
documents/ (PDF with `pdftotext -layout`, DOCX with the standard library, .md/.txt copied)
into .text/<path>.txt; pages are marked `=== page N ===` so citations can say "p. N".
A document that cannot be read is recorded as not indexed, with the reason; it never
stops the others. Unchanged documents (same sha256) are skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from labpipe.provenance import sha256_file, utc_now
from labpipe.safety import PROJECT_ROOT, atomic_write_text

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "context"
FILES = ("context.md", "instructions.md")
INDEX_SCHEMA = "labpipe.context.index/1"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def folder() -> Path:
    return PROJECT_ROOT / "context"


def init(config: dict | None = None) -> dict[str, Any]:
    base = folder()
    created, kept = [], []
    for directory in (base, base / "documents"):
        (kept if directory.is_dir() else created).append(directory.relative_to(PROJECT_ROOT).as_posix())
        directory.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = base / name
        label = target.relative_to(PROJECT_ROOT).as_posix()
        if target.exists():
            kept.append(label)
            continue
        atomic_write_text(target, (TEMPLATES / name).read_text(encoding="utf-8"), config or None,
                          pipeline=False)
        created.append(label)
    return {"created": created, "kept": kept,
            "next": ["write context/context.md (or ask your agent: let's write the project context)",
                     "put the work plan and papers in context/documents/, then `labpipe context index`"]}


def _pdf_text(path: Path) -> tuple[str, int]:
    tool = shutil.which("pdftotext")
    if not tool:
        raise RuntimeError("pdftotext is not installed (package poppler-utils; it is in the "
                           "container image)")
    done = subprocess.run([tool, "-layout", str(path), "-"], capture_output=True, timeout=300)
    if done.returncode != 0:
        raise RuntimeError("pdftotext failed: " + done.stderr.decode("utf-8", "replace").strip()[:200])
    pages = done.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return "".join(f"=== page {n} ===\n{page.strip(chr(10))}\n\n"
                   for n, page in enumerate(pages, 1)), len(pages)


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise RuntimeError(f"not a readable DOCX ({type(exc).__name__})") from exc
    lines = []
    for paragraph in root.iter(f"{_W}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines) + "\n"


def _extract(path: Path) -> tuple[str, int | None]:
    kind = path.suffix.lower()
    if kind == ".pdf":
        return _pdf_text(path)
    if kind == ".docx":
        return _docx_text(path), None
    if kind in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace"), None
    raise RuntimeError(f"file type {kind or '(none)'} is not supported (PDF, DOCX, md, txt)")


def documents() -> list[Path]:
    base = folder() / "documents"
    return sorted(p for p in base.rglob("*") if p.is_file()
                  and not any(part.startswith(".") for part in p.relative_to(base).parts))


def load_index() -> dict[str, Any]:
    try:
        return json.loads((folder() / ".text" / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": INDEX_SCHEMA, "documents": {}}


def index(config: dict | None = None) -> dict[str, Any]:
    base = folder()
    if not (base / "documents").is_dir():
        return {"error": "no context/documents/ folder: run `labpipe context init` first"}
    old = load_index().get("documents", {})
    found: dict[str, Any] = {}
    for path in documents():
        relative = path.relative_to(base / "documents").as_posix()
        sha = sha256_file(path)
        target = base / ".text" / f"{relative}.txt"
        before = old.get(relative) or {}
        if before.get("sha256") == sha and before.get("status") == "indexed" and target.exists():
            found[relative] = {**before, "unchanged": True}
            continue
        try:
            text, pages = _extract(path)
            atomic_write_text(target, text, config or None, pipeline=False)
            found[relative] = {"sha256": sha, "status": "indexed", "pages": pages,
                               "text": target.relative_to(PROJECT_ROOT).as_posix()}
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            found[relative] = {"sha256": sha, "status": "not indexed", "reason": str(exc),
                               "pages": None, "text": None}
    removed = sorted(set(old) - set(found))
    record = {"schema": INDEX_SCHEMA, "utc": utc_now(),
              "documents": {k: {a: b for a, b in v.items() if a != "unchanged"}
                            for k, v in found.items()}}
    atomic_write_text(base / ".text" / "index.json",
                      json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                      config or None, pipeline=False)
    return {"indexed": sorted(k for k, v in found.items()
                              if v["status"] == "indexed" and not v.get("unchanged")),
            "unchanged": sorted(k for k, v in found.items() if v.get("unchanged")),
            "not_indexed": {k: v["reason"] for k, v in found.items()
                            if v["status"] != "indexed"},
            "no_longer_in_documents": removed,
            "index": (base / ".text" / "index.json").relative_to(PROJECT_ROOT).as_posix()}
