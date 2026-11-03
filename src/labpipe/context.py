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

What is read is bounded, because the text goes into the postdoc's prompts: links (to a
file or a folder) and anything resolving outside documents/ are never read; a document, an
unpacked DOCX, a PDF run, the extracted text and DOCX tables have limits (constants below).
Over a limit: not indexed, or truncated with a visible marker, with the reason recorded.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from labpipe.provenance import sha256_file, utc_now
from labpipe.safety import CODE_ROOT, PROJECT_ROOT, atomic_write_text

TEMPLATES = CODE_ROOT / "templates" / "context"
FILES = ("context.md", "instructions.md")
INDEX_SCHEMA = "labpipe.context.index/1"

# Limits. Each keeps one careless or hostile file from filling the disk, the memory or the prompt.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024       # a work plan or paper is far smaller; bigger is likely not text
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024       # uncompressed word/document.xml: a zip bomb is small packed, huge unpacked
MAX_DOCX_ENTRIES = 1000                     # a real DOCX has tens of parts; thousands of entries is a bomb
PDFTOTEXT_TIMEOUT_S = 120                   # a damaged PDF can make pdftotext run for ever
MAX_PDF_PAGES = 500                         # pdftotext -l: a book is not a project document
MAX_TEXT_CHARS = 1_000_000                  # extracted text per document: what one prompt can sensibly hold
MAX_TABLE_ROWS = 200                        # DOCX table rows kept per table
MAX_CELL_CHARS = 500                        # DOCX table cell text kept per cell
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


PROVIDER_NOTE = ("what the postdoc reads (context.md, the documents' text, the results) is sent to your "
                 "agent tool's AI provider (e.g. Anthropic for Claude Code, OpenAI for Codex): do not "
                 "put documents there that may not leave the lab")


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
            "note": PROVIDER_NOTE,
            "next": ["write context/context.md (or ask your agent: let's write the project context)",
                     "put the work plan and papers in context/documents/, then `labpipe context index`"]}


def _pdf_text(path: Path) -> tuple[str, int]:
    tool = shutil.which("pdftotext")
    if not tool:
        raise RuntimeError("pdftotext is not installed (package poppler-utils; it is in the "
                           "container image)")
    try:
        done = subprocess.run([tool, "-layout", "-l", str(MAX_PDF_PAGES), str(path), "-"],
                              capture_output=True, timeout=PDFTOTEXT_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pdftotext took longer than {PDFTOTEXT_TIMEOUT_S} s: not read") from exc
    if done.returncode != 0:
        raise RuntimeError("pdftotext failed: " + done.stderr.decode("utf-8", "replace").strip()[:200])
    pages = done.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return "".join(f"=== page {n} ===\n{page.strip(chr(10))}\n\n"
                   for n, page in enumerate(pages, 1)), len(pages)


def _cell_text(cell) -> str:
    text = " ".join("".join(t.text or "" for t in p.iter(f"{_W}t")) for p in cell.iter(f"{_W}p"))
    text = " ".join(text.split())
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "…"


def _table_lines(table) -> list[str]:
    """One line per row, cells joined with ` | `; rows and cell text bounded, with a marker."""
    rows = table.findall(f"{_W}tr")
    lines = [" | ".join(_cell_text(c) for c in row.findall(f"{_W}tc")) for row in rows[:MAX_TABLE_ROWS]]
    if len(rows) > MAX_TABLE_ROWS:
        lines.append(f"=== table truncated at {MAX_TABLE_ROWS} of {len(rows)} rows ===")
    return lines


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_DOCX_ENTRIES:
                raise RuntimeError(f"DOCX has {len(infos)} entries (limit {MAX_DOCX_ENTRIES}): not read")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise RuntimeError(f"DOCX text part is {info.file_size // 2**20} MB unpacked "
                                   f"(limit {MAX_DOCX_XML_BYTES // 2**20} MB): not read")
            with archive.open(info) as part:     # the header can lie: read at most the limit + 1
                data = part.read(MAX_DOCX_XML_BYTES + 1)
            if len(data) > MAX_DOCX_XML_BYTES:
                raise RuntimeError(f"DOCX text part is over {MAX_DOCX_XML_BYTES // 2**20} MB "
                                   "unpacked: not read")
            root = ElementTree.fromstring(data)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise RuntimeError(f"not a readable DOCX ({type(exc).__name__})") from exc
    lines: list[str] = []

    def walk(node) -> None:
        for child in node:
            if child.tag == f"{_W}tbl":
                lines.extend(_table_lines(child))
            elif child.tag == f"{_W}p":
                parts = []
                for item in child.iter():
                    if item.tag == f"{_W}t":
                        parts.append(item.text or "")
                    elif item.tag == f"{_W}tab":
                        parts.append("\t")
                    elif item.tag in (f"{_W}br", f"{_W}cr"):
                        parts.append("\n")
                lines.append("".join(parts))
            else:
                walk(child)

    walk(root)
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
    """Every non-hidden file and link under documents/ (links are listed, never followed)."""
    base = folder() / "documents"
    found = []
    for top, dirs, files in os.walk(base, followlinks=False):
        here = Path(top)
        # a link to a folder shows up in `dirs` but is not descended into: list it as one entry
        found += [here / name for name in files + [d for d in dirs if (here / d).is_symlink()]]
        dirs[:] = [d for d in dirs if not (here / d).is_symlink()]
    return sorted(p for p in found
                  if not any(part.startswith(".") for part in p.relative_to(base).parts))


def link_problem(path: Path, base: Path) -> str | None:
    """Why `path` must not be read: a link on its way (context/ or documents/ itself included),
    or a resolved place outside documents/ or the project."""
    if base.is_symlink() or base.parent.is_symlink():
        return "context/documents is a link: not read (copy the files into the project)"
    try:
        base.resolve().relative_to(PROJECT_ROOT.resolve())
    except (ValueError, OSError):
        return "context/documents is outside the project: not read"
    relative = path.relative_to(base)
    here = base
    for part in relative.parts:
        here = here / part
        if here.is_symlink():
            return "a link to outside context/documents/: not read"
    try:
        path.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return "a link to outside context/documents/: not read"
    if not path.is_file():
        return "not a regular file: not read"
    return None


def _bounded(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS].rstrip("\n") + f"\n=== truncated at {MAX_TEXT_CHARS} characters ===\n", True


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
        problem = link_problem(path, base / "documents")
        if not problem and path.stat().st_size > MAX_DOCUMENT_BYTES:
            problem = (f"{path.stat().st_size // 2**20} MB, over the limit of "
                       f"{MAX_DOCUMENT_BYTES // 2**20} MB: not read")
        if problem:
            found[relative] = {"sha256": None, "status": "not indexed", "reason": problem,
                               "pages": None, "text": None}
            continue
        sha = sha256_file(path)
        target = base / ".text" / f"{relative}.txt"
        before = old.get(relative) or {}
        if before.get("sha256") == sha and before.get("status") == "indexed" and target.exists():
            found[relative] = {**before, "unchanged": True}
            continue
        try:
            text, pages = _extract(path)
            text, truncated = _bounded(text)
            if pages == MAX_PDF_PAGES:      # pdftotext stopped at the page limit
                text += f"=== truncated at {MAX_PDF_PAGES} pages ===\n"
                truncated = True
            atomic_write_text(target, text, config or None, pipeline=False)
            found[relative] = {"sha256": sha, "status": "indexed", "pages": pages,
                               "truncated": truncated,
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
            "truncated": sorted(k for k, v in found.items() if v.get("truncated")),
            "no_longer_in_documents": removed,
            "index": (base / ".text" / "index.json").relative_to(PROJECT_ROOT).as_posix()}
