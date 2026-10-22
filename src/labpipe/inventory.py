"""`labpipe inventory`: what labpipe recognises in the data folder(s). Reads only.

Per raw root: files per method (by extension), files no method recognises (by
extension, with examples), file names that give no sample ID, and how many
files the manifest already knows. Setup shows this to the user and asks whether
it is right; agents use it to ask the user about anything unrecognised.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from labpipe import manifest as mf
from labpipe import migrations
from labpipe.scanner import sample_id_for
from labpipe.registry import classify, load_methods, skip_reason, user_ignore
from labpipe.safety import PROJECT_ROOT


def _overrides(config: dict) -> dict[str, str]:
    path = PROJECT_ROOT / config["sample_id"]["overrides_file"]
    if not path.exists():
        return {}
    data = migrations.upgraded(path, yaml.safe_load(path.read_text(encoding="utf-8")) or {},
                               PROJECT_ROOT)
    return data.get("overrides") or {}


def data_folder(config: dict, root: dict) -> str | None:
    """Where the user adds files for this raw root (the writable original), if known."""
    if root.get("source"):
        return root["source"]
    protected = config.get("protected_paths") or []
    return protected[0] if len(config.get("raw_roots") or []) == 1 and len(protected) == 1 \
        else None


def inventory(config: dict, examples: int = 3) -> dict[str, Any]:
    methods = load_methods()
    ignored = user_ignore()
    overrides = _overrides(config)
    known = Counter(e["raw_root"] for e in mf.load(config)["entries"].values())
    roots = []
    for root in config["raw_roots"]:
        path = Path(os.path.expanduser(root["path"]))
        out: dict[str, Any] = {"id": root["id"], "reads_from": str(path),
                               "data_folder": data_folder(config, root)}
        if not path.is_dir():
            roots.append({**out, "available": False})
            continue
        by_method: dict[str, Counter[str]] = defaultdict(Counter)
        method_examples: dict[str, list[str]] = defaultdict(list)
        unknown: Counter[str] = Counter()
        unknown_examples: dict[str, list[str]] = defaultdict(list)
        unclear: list[str] = []
        skipped: Counter[str] = Counter()
        for directory, subdirectories, names in os.walk(path):
            subdirectories.sort()
            for name in sorted(names):
                relative = (Path(directory) / name).relative_to(path).as_posix()
                extension = Path(name).suffix.lower() or "(none)"
                skip = skip_reason(relative, config, ignored)
                if skip:
                    skipped[skip] += 1
                    continue
                method, kind = classify(relative, methods, root=path)
                if method is None:
                    unknown[extension] += 1
                    if len(unknown_examples[extension]) < examples:
                        unknown_examples[extension].append(relative)
                    continue
                by_method[method][f"{extension} ({'analysed' if kind == 'analyse' else 'ignored'})"] += 1
                if kind == "analyse":
                    if len(method_examples[method]) < examples:
                        method_examples[method].append(relative)
                    _, _, problem = sample_id_for(relative, methods[method], config, overrides)
                    if problem:
                        unclear.append(relative)
        roots.append({**out, "available": True,
                      "recognised": {m: {"files": dict(c), "examples": method_examples[m]}
                                     for m, c in sorted(by_method.items())},
                      "not_recognised": {ext: {"files": n, "examples": unknown_examples[ext]}
                                         for ext, n in unknown.most_common()},
                      "left_out": dict(skipped),
                      "no_sample_id": {"files": len(unclear), "examples": unclear[:8],
                                       "pattern": config["sample_id"]["pattern"]},
                      "already_in_manifest": known.get(root["id"], 0)})
    return {"roots": roots}


def describe(result: dict[str, Any]) -> str:
    """Plain-text version for the terminal (setup)."""
    lines = []
    for root in result["roots"]:
        lines.append(f"Data location '{root['id']}'")
        if root.get("data_folder"):
            lines.append(f"  you add files to:       {root['data_folder']}")
        lines.append(f"  labpipe reads them from: {root['reads_from']} (read-only)")
        if not root["available"]:
            lines.append("  not available (not mounted?)")
            continue
        if root["recognised"]:
            lines.append("  Recognised:")
            for method, info in root["recognised"].items():
                counts = ", ".join(f"{n} {kind}" for kind, n in info["files"].items())
                lines.append(f"    {method}: {counts}   e.g. {', '.join(info['examples'][:2])}")
        else:
            lines.append("  Recognised: nothing")
        if root["not_recognised"]:
            lines.append("  Not recognised (no method reads these; your own analyses, figures and "
                         "tables: `labpipe methods import`):")
            for ext, info in list(root["not_recognised"].items())[:12]:
                lines.append(f"    {ext}: {info['files']}   e.g. {', '.join(info['examples'][:2])}")
        if root.get("left_out"):
            lines.append("  Left out: " + ", ".join(f"{n} {why}" for why, n in root["left_out"].items()))
        if root["no_sample_id"]["files"]:
            lines.append(f"  No sample ID from the file name ({root['no_sample_id']['files']}): "
                         + ", ".join(root["no_sample_id"]["examples"][:4]))
    return "\n".join(lines)
