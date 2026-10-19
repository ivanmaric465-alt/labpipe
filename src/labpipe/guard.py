"""PreToolUse hook for Claude Code and Codex: `labpipe guard` (hook event on stdin).

Both harnesses send {"tool_name", "tool_input", "cwd"} and block on exit code 2 with
the reason on stderr. File edits arrive as Write/Edit/... (Claude Code) or as
`apply_patch` with the patch text in tool_input.command (Codex; every path in its
"*** Add/Update/Delete File:" and "*** Move to:" lines is checked); shell commands
as Bash (both).

One safety layer among several (OS read-only mount, labpipe's own path checks,
Claude Code permissions and sandbox). It blocks, whatever the permission
settings say:
* any file write or edit inside a raw root or a protected path;
* agent edits of pipeline-made files: analysis/** (except notes.md) and the manifest;
* shell commands that name a raw root or protected path.
In unattended mode (LABPIPE_AGENT_MODE=unattended, set by the scheduled-run
wrapper) it additionally allows only notes.md and logs/ writes, and only plain
`labpipe ...` commands without shell operators, plus read-only file viewing
(cat, head, tail, sed -n, ls, wc, grep, rg, nl) of paths inside the project:
Codex has no separate read tool and reads files through the shell.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from labpipe.safety import PROJECT_ROOT, SetupError, load_pipeline_config

FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
SHELL_TOOLS = {"Bash", "exec_command", "shell", "local_shell"}
PATCH_PATHS = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)
SHELL_OPERATORS = re.compile(r"[;&|<>`]|\$\(|\n")
# Unattended runs: what a pipeline pass needs, nothing that changes setup or state otherwise.
UNATTENDED_COMMANDS = {"doctor", "scan", "pending", "status", "notes", "review", "report"}
UNATTENDED_METHOD_ACTIONS = {"analyze", "check", "summary"}
READ_ONLY_COMMANDS = {"cat", "head", "tail", "sed", "ls", "wc", "grep", "rg", "nl"}


def _real(path: str | Path, cwd: str | None = None) -> Path:
    path = Path(os.path.expanduser(str(path)))
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    return Path(os.path.realpath(path))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _protected(config: dict | None) -> list[Path]:
    if not config:
        return []
    roots = [item["path"] for item in config.get("raw_roots", [])] + list(
        config.get("protected_paths", []))
    return [_real(root) for root in roots]


def decide(event: dict[str, Any], config: dict | None, mode: str) -> str | None:
    """Return a reason to block the tool call, or None to let normal permissions decide."""
    tool = event.get("tool_name", "")
    data = event.get("tool_input") or {}
    cwd = event.get("cwd")
    protected = _protected(config)
    project = _real(PROJECT_ROOT)

    if tool in FILE_TOOLS:
        return _file_decision(_real(data.get(FILE_TOOLS[tool], ""), cwd), config, mode,
                              protected, project)
    if tool == "apply_patch":
        targets = PATCH_PATHS.findall(str(data.get("command") or data.get("input") or ""))
        if not targets:
            return "unreadable apply_patch input; blocking to be safe"
        for target in targets:
            reason = _file_decision(_real(target, cwd), config, mode, protected, project)
            if reason:
                return reason
        return None
    if tool in SHELL_TOOLS:
        return _shell_decision(data.get("command", ""), config, mode, protected, cwd)
    return None


def _file_decision(target: Path, config: dict | None, mode: str, protected: list[Path],
                   project: Path) -> str | None:
    for root in protected:
        if _inside(target, root):
            return f"{target} is inside protected/raw path {root}; raw data is read-only"
    analysis = project / ((config or {}).get("analysis_dir") or "analysis")
    logs = project / ((config or {}).get("logs_dir") or "logs")
    manifest = project / ((config or {}).get("manifest") or "manifest.json")
    is_notes = _inside(target, analysis) and target.name == "notes.md"
    if target in (manifest, manifest.with_suffix(".lock")):
        return "manifest.json is written only by labpipe commands"
    if _inside(target, analysis) and not is_notes:
        return ("files under analysis/ are produced by labpipe; agents write only notes.md "
                "(change settings and rerun instead)")
    if mode == "unattended" and not (is_notes or _inside(target, logs)):
        return "unattended runs may write only notes.md and logs/"
    if config is None and not _inside(target, project):
        return "labpipe is not set up yet (no config/local.yaml): writes outside the project refused"
    return None


def _unwrap_shell(command: str) -> str:
    """Codex may report `bash -lc '<cmd>'`; judge the inner command."""
    match = re.fullmatch(r"(?:/\S*/)?(?:ba|z)?sh\s+-l?c\s+(['\"])(.*)\1", command, re.S)
    return match.group(2).strip() if match else command


def _read_only_decision(command: str, cwd: str | None) -> str | None:
    """cat/head/sed -n/rg … of paths inside the project only; never sed -i or find -exec."""
    try:
        words = shlex.split(command)
    except ValueError:
        return "unreadable command (quoting); blocking to be safe"
    if words[0] == "sed" and not ("-n" in words and not any(w.startswith("-i") or w == "--in-place"
                                                            for w in words)):
        return "unattended runs may use sed only as `sed -n` (read-only)"
    project = _real(PROJECT_ROOT)
    for word in words[1:]:
        if word.startswith("-"):
            continue
        if word.startswith(("/", "~")) or ".." in Path(word).parts:
            if not _inside(_real(word, cwd), project):
                return f"unattended runs may read files inside the project only, not {word}"
    return None


def _shell_decision(command: Any, config: dict | None, mode: str,
                    protected: list[Path], cwd: str | None = None) -> str | None:
    command = " ".join(command) if isinstance(command, list) else str(command)
    expanded = os.path.expanduser(command)
    for root in protected:
        for spelling in {str(root), str(root).replace("\\", "/")}:
            if spelling and spelling in expanded:
                return (f"command names protected/raw path {root}; read raw data only "
                        "through labpipe commands")
    if config:
        for item in config.get("raw_roots", []) + [{"path": p} for p in config.get(
                "protected_paths", [])]:
            written = os.path.expanduser(str(item["path"]))
            if written and written in expanded:
                return (f"command names protected/raw path {written}; read raw data only "
                        "through labpipe commands")
    if mode == "unattended":
        stripped = _unwrap_shell(command.strip())
        if SHELL_OPERATORS.search(stripped):
            return "unattended runs may only run plain commands (no ; & | < > ` $( or newlines)"
        if stripped.split()[:1] and stripped.split()[0] in READ_ONLY_COMMANDS:
            return _read_only_decision(stripped, cwd)
        if not re.match(r"^labpipe(\s|$)", stripped):
            return "unattended runs may only run plain `labpipe ...` commands (and read files)"
        words = stripped.split()[1:]
        if words[:2] == ["notes", "retire"]:
            return "unattended runs may not retire notes (a user decision)"
        if "--baseline" in words:
            return "unattended runs may not mark files as baseline (a user decision)"
        if not words or not (words[0] in UNATTENDED_COMMANDS or (
                len(words) > 1 and words[1] in UNATTENDED_METHOD_ACTIONS)):
            return ("unattended runs may only run pipeline commands: "
                    + ", ".join(sorted(UNATTENDED_COMMANDS))
                    + ", <method> " + "|".join(sorted(UNATTENDED_METHOD_ACTIONS)))
    return None


def main() -> int:
    """Hook entry point. Exit 2 + stderr blocks the call; exit 0 defers to permissions."""
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("labpipe guard: unreadable hook input; blocking to be safe", file=sys.stderr)
        return 2
    try:
        config = load_pipeline_config()
    except SetupError:
        config = None
    mode = os.environ.get("LABPIPE_AGENT_MODE", "interactive")
    reason = decide(event, config, mode)
    if reason:
        print(f"labpipe guard: {reason}", file=sys.stderr)
        return 2
    return 0
