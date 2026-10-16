"""Path safety: where the pipeline may write, and atomic writes.

Rules (enforced here, independent of Claude Code permissions and the OS
read-only mount, which are further layers):
* nothing is ever written inside a raw root or a protected path;
* pipeline writes go only to <root>/analysis, <root>/logs and <root>/manifest.json;
* every write is atomic (temp file in the same directory + os.replace);
* nothing is deleted; superseded outputs are moved within analysis/.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(os.environ.get("LABPIPE_ROOT", Path(__file__).resolve().parents[2]))


class UnsafePathError(RuntimeError):
    pass


class SetupError(RuntimeError):
    pass


def load_pipeline_config() -> dict:
    """config/pipeline.yaml (shared policy) + config/local.yaml (this machine's paths)."""
    with open(PROJECT_ROOT / "config" / "pipeline.yaml", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    local = PROJECT_ROOT / "config" / "local.yaml"
    if not local.exists():
        raise SetupError("config/local.yaml is missing: run `labpipe init` and edit it to "
                         "point at your (read-only) raw data")
    with open(local, encoding="utf-8") as stream:
        machine = yaml.safe_load(stream) or {}
    config["raw_roots"] = machine.get("raw_roots") or []
    config["protected_paths"] = machine.get("protected_paths") or []
    config["agents"] = machine.get("agents")
    config["automation"] = machine.get("automation")
    if (config["automation"] or {}).get("settle_minutes") is not None:
        config["settle_minutes"] = config["automation"]["settle_minutes"]  # this machine's choice
    override = os.environ.get("LABPIPE_RAW_ROOT")
    if override:
        # Same data, another path: keep the configured id (if there is exactly one root)
        # so manifest entries stay the same when the override is dropped later.
        configured = config["raw_roots"]
        root_id = configured[0]["id"] if len(configured) == 1 else "override"
        config["raw_roots"] = [{"id": root_id, "path": override,
                                "note": "LABPIPE_RAW_ROOT override"}]
    return config


def set_local_block(name: str, value: Any) -> None:
    """Replace the top-level `name:` block of config/local.yaml in place (append it if
    absent). Other blocks and comments between blocks are kept; comments inside the
    replaced block are not. Used by the interactive setup commands, never by agents."""
    path = PROJECT_ROOT / "config" / "local.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"{name}:")), None)
    block = yaml.safe_dump({name: value}, sort_keys=False, allow_unicode=True,
                           width=1000).splitlines()
    if start is None:
        lines = [*lines, *block]
    else:
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace()):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1  # keep blank lines that separate blocks
        lines[start:end] = block
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n", pipeline=False)


def _resolved(path: Path | str) -> Path:
    return Path(os.path.realpath(path))


def protected_roots(config: dict | None = None) -> list[Path]:
    if config is None:
        try:
            config = load_pipeline_config()
        except SetupError:  # no local.yaml yet: nothing is configured as protected
            return []
    roots = [_resolved(item["path"]) for item in config.get("raw_roots", [])]
    roots += [_resolved(os.path.expanduser(item)) for item in config.get("protected_paths", [])]
    return roots


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def assert_not_protected(path: Path | str, config: dict | None = None) -> Path:
    target = _resolved(path)
    for root in protected_roots(config):
        if _inside(target, root):
            raise UnsafePathError(f"refusing to write inside protected path {root}: {target}")
    return target


def assert_pipeline_writable(path: Path | str, config: dict | None = None) -> Path:
    config = config or load_pipeline_config()
    target = assert_not_protected(path, config)
    allowed = [_resolved(PROJECT_ROOT / config["analysis_dir"]),
               _resolved(PROJECT_ROOT / config["logs_dir"])]
    manifest = _resolved(PROJECT_ROOT / config["manifest"])
    if target == manifest:
        return target
    if any(_inside(target, root) for root in allowed):
        return target
    raise UnsafePathError(f"pipeline may only write under analysis/, logs/ or the manifest: "
                          f"{target}")


def atomic_write_bytes(path: Path, data: bytes, config: dict | None = None,
                       *, pipeline: bool = True) -> None:
    target = (assert_pipeline_writable(path, config) if pipeline
              else assert_not_protected(path, config))
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def atomic_write_text(path: Path, text: str, config: dict | None = None,
                      *, pipeline: bool = True) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), config, pipeline=pipeline)
