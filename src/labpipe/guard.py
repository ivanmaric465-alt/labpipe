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
* agent edits of pipeline-made files: analysis/** (except notes.md and the postdoc's
  analysis/_postdoc/<run>/analysis.md) and the manifest;
* in a project: any write to the files `labpipe project sync` generates (AGENTS.md, CLAUDE.md,
  .labpipe/, the harness's settings and agent files; the list is .labpipe/generated.json);
  the lab's additions go in lab/LAB.md and lab/agents/;
* any write to context/documents/** (the project's documents are read-only, like raw data)
  and context/.text/** (made by `labpipe context index`);
* shell commands that name a raw root or protected path.
In unattended mode (LABPIPE_AGENT_MODE=unattended, set by the scheduled-run
wrapper) it additionally allows only notes.md and logs/ writes, and only plain
`labpipe ...` commands without shell operators, plus read-only file viewing
(cat, head, tail, sed -n, ls, wc, grep, rg, nl; only the forms in READ_FORMS, no `$`) of
paths inside the project: Codex has no separate read tool and reads files through the shell.
Mode "postdoc" (`labpipe postdoc run`) is the same, but writes only the run's analysis.md and
runs only read-only labpipe commands plus `labpipe postdoc verify|list`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from labpipe.safety import (FIXED_GENERATED, GENERATED_DIR, GENERATED_RECORD, PROJECT_ROOT,
                            SetupError, is_dev, load_pipeline_config)

FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
SHELL_TOOLS = {"Bash", "exec_command", "shell", "local_shell"}
PATCH_PATHS = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)
SHELL_OPERATORS = re.compile(r"[;&|<>`]|\$\(|\n")
# Unattended runs: what a pipeline pass needs, nothing that changes setup or state otherwise.
UNATTENDED_COMMANDS = {"doctor", "scan", "pending", "status", "notes", "review", "report"}
UNATTENDED_METHOD_ACTIONS = {"analyze", "check", "summary"}
READ_ONLY_COMMANDS = {"cat", "head", "tail", "sed", "ls", "wc", "grep", "rg", "nl"}
# The postdoc's headless run: reads and compares, writes only its analysis.md.
POSTDOC_COMMANDS = {"doctor", "status", "dataset"}
POSTDOC_ACTIONS = {"notes": {"todo", "verify"}, "postdoc": {"verify", "list"}}
UNATTENDED_MODES = {"unattended", "postdoc"}


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


def _generated(project: Path) -> set[str]:
    try:
        record = json.loads((project / GENERATED_RECORD).read_text(encoding="utf-8"))
        return set(record.get("files") or {})
    except (OSError, ValueError, AttributeError):
        return set()


def _generated_reason(target: Path, project: Path) -> str | None:
    if is_dev(project) or not _inside(target, project):
        return None
    relative = target.relative_to(project).as_posix()
    if (_inside(target, project / GENERATED_DIR) or relative in FIXED_GENERATED
            or relative in _generated(project)):
        return (f"{relative} is generated by labpipe and rewritten by `labpipe project sync`; "
                "the lab's additions go in lab/LAB.md and lab/agents/")
    return None


def _file_decision(target: Path, config: dict | None, mode: str, protected: list[Path],
                   project: Path) -> str | None:
    for root in protected:
        if _inside(target, root):
            return f"{target} is inside protected/raw path {root}; raw data is read-only"
    reason = _generated_reason(target, project)
    if reason:
        return reason
    analysis = project / ((config or {}).get("analysis_dir") or "analysis")
    logs = project / ((config or {}).get("logs_dir") or "logs")
    manifest = project / ((config or {}).get("manifest") or "manifest.json")
    is_notes = _inside(target, analysis) and target.name == "notes.md"
    is_postdoc = (target.name == "analysis.md" and target.parent.parent == analysis / "_postdoc")
    context = project / "context"
    if _inside(target, context / "documents") or _inside(target, context / ".text"):
        return ("context/documents/ is read-only for agents (the user's files); "
                "context/.text/ is made by `labpipe context index`")
    if target in (manifest, manifest.with_suffix(".lock")):
        return "manifest.json is written only by labpipe commands"
    if _inside(target, analysis) and not (is_notes or is_postdoc):
        return ("files under analysis/ are produced by labpipe; agents write only notes.md "
                "and the postdoc's analysis.md (change settings and rerun instead)")
    if mode == "postdoc" and not is_postdoc:
        return "the postdoc's headless run may write only its analysis.md"
    if mode == "unattended" and not (is_notes or _inside(target, logs)):
        return "unattended runs may write only notes.md and logs/"
    if config is None and not _inside(target, project):
        return "labpipe is not set up yet (no config/local.yaml): writes outside the project refused"
    return None


def _unwrap_shell(command: str) -> str:
    """Codex may report `bash -lc '<cmd>'`; judge the inner command."""
    match = re.fullmatch(r"(?:/\S*/)?(?:ba|z)?sh\s+-l?c\s+(['\"])(.*)\1", command, re.S)
    return match.group(2).strip() if match else command


# Read-only viewing (unattended and postdoc runs): one strict form per command. Each entry:
# short flags without an argument, short options taking a number, bare -N (head/tail),
# short options taking a word, long flags, long options (--name=VALUE), what it may omit.
READ_FORMS = {
    "cat": ("n", "", False, "", (), (), "cat [-n] FILE..."),
    "nl": ("", "", False, "", (), (), "nl FILE..."),
    "wc": ("lwc", "", False, "", (), (), "wc [-l -w -c] FILE..."),
    "head": ("", "n", True, "", (), (), "head [-n N | -N] FILE..."),
    "tail": ("", "n", True, "", (), (), "tail [-n N | -N] FILE..."),
    "ls": ("lah1Rtr", "", False, "", (), (), "ls [-l -a -h -1 -R -t -r] [PATH...]"),
    "grep": ("nilcFwvr", "ABC", False, "e", ("--no-heading",), ("--include",),
             "grep [-n -i -l -c -F -w -v -r -A/-B/-C N --include=GLOB] PATTERN|-e PATTERN [PATH...]"),
    "rg": ("nilcFwv", "ABC", False, "ge", ("--no-heading",), (),
           "rg [-n -i -l -c -F -w -v -A/-B/-C N --no-heading -g GLOB] PATTERN|-e PATTERN [PATH...]"),
}
SED_FORM = "sed -n 'Np' | 'N,Mp' | 'N,$p' FILE..."
SED_PROGRAM = re.compile(r"\d+(,(\d+|\$))?p")
DIGITS = re.compile(r"\d+")
SEARCHES = {"grep", "rg"}


def _unquoted_brace(command: str) -> bool:
    """A `{` outside quotes (the shell would expand `a{,/etc/x}`)."""
    quote, skip = "", False
    for char in command:
        if skip:
            skip = False
        elif quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "\\":
            skip = True
        elif char == "{":
            return True
    return False


def _parse_options(command: str, words: list[str]) -> tuple[list[str], list[str], str | None]:
    """(operands, -e patterns, problem) of a command's words by its READ_FORMS entry."""
    flags, numeric, bare, valued, longs, long_values, _ = READ_FORMS[command]
    operands, patterns, index = [], [], 0
    while index < len(words):
        word = words[index]
        index += 1
        if word == "-" or not word.startswith("-"):
            operands.append(word)
        elif word.startswith("--"):
            if not (word in longs or ("=" in word and word.split("=")[0] in long_values)):
                return operands, patterns, f"option {word} is not allowed"
        elif bare and DIGITS.fullmatch(word[1:]):
            continue
        else:
            for position, char in enumerate(word[1:], 1):
                if char in flags:
                    continue
                if char not in numeric + valued:
                    return operands, patterns, f"option -{char} is not allowed"
                value = word[position + 1:]
                if not value and index < len(words):
                    value, index = words[index], index + 1
                if char in numeric and not DIGITS.fullmatch(value):
                    return operands, patterns, f"-{char} needs a number"
                if char == "e":
                    patterns.append(value)
                break
    return operands, patterns, None


def _path_problem(operands: list[str], cwd: str | None, project: Path) -> str | None:
    for word in operands:
        if word.startswith("~") and not word.startswith("~/") and word != "~":
            return f"{word}: only ~ and ~/... are understood"
        if "{" in word or (".." in Path(word).parts and re.search(r"[*?\[]", word)):
            return f"{word}: no braces, and no wildcards combined with .."
        if not _inside(_real(word, cwd), project):
            return f"{word} is outside the project"
    return None


def _read_only_decision(command: str, cwd: str | None) -> str | None:
    """cat/head/sed -n/rg … in the strict READ_FORMS, of paths inside the project only."""
    try:
        words = shlex.split(command)
    except ValueError:
        return "unreadable command (quoting); blocking to be safe"
    name, words = words[0], words[1:]
    form = SED_FORM if name == "sed" else READ_FORMS[name][-1]

    def refuse(why: str) -> str:
        return (f"unattended runs may read files only as `{form}` with paths inside the project "
                f"(no $ except in a quoted 'N,$p', no ~user, braces or other options): {why}")

    program = words[1] if name == "sed" and len(words) > 1 else ""
    dollars = command.count("$") - (program.count("$") if f"'{program}'" in command else 0)
    if dollars or "\n" in command or _unquoted_brace(command):
        return refuse("shell expansion ($, braces, newlines) is not allowed")
    project = _real(PROJECT_ROOT)
    if name == "sed":
        if len(words) < 3 or words[0] != "-n" or not SED_PROGRAM.fullmatch(words[1]):
            return refuse("sed takes exactly -n, one print program and files")
        operands, patterns, problem = words[2:], [], None
    else:
        operands, patterns, problem = _parse_options(name, words)
    if problem:
        return refuse(problem)
    if name in SEARCHES:
        if len(patterns) > 1 or (not patterns and not operands):
            return refuse("exactly one pattern is needed")
        if not patterns:
            operands = operands[1:]
    recursive = name in ("ls", "rg") or (name == "grep" and any(
        w.startswith("-") and not w.startswith("--") and "r" in w for w in words))
    if not operands:
        if not recursive:
            return refuse("a file is needed")
        operands = ["."]
    problem = _path_problem(operands, cwd, project)
    return refuse(problem) if problem else None


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
    if mode in UNATTENDED_MODES:
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
        if words[:1] == ["update"]:
            return "unattended runs may not update labpipe (the user runs `labpipe update`)"
        if mode == "postdoc":
            if not words or not (words[0] in POSTDOC_COMMANDS or (len(words) > 1 and (
                    words[1] in POSTDOC_ACTIONS[words[0]] if words[0] in POSTDOC_ACTIONS
                    else words[1] in {"summary", "check"}))):
                return ("the postdoc's headless run may only run: "
                        + ", ".join(sorted(POSTDOC_COMMANDS)) + ", <method> summary|check, "
                        "notes verify, postdoc verify|list")
            return None
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
