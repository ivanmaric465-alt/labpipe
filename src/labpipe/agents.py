"""`labpipe agents sync`: harness-specific agent files from the neutral roles in agents/.

agents/<name>.md holds each role once: frontmatter (name, role, description,
capabilities, delegates_to, writes, optional harness_extras) and the instructions.
harness_extras.<harness> is copied verbatim into that harness's frontmatter, so
harness-only features (Claude Code: maxTurns, hooks, skills, ...) stay usable. config/local.yaml →
agents chooses the harness and a model per role:

    agents:
      harness: claude-code           # claude-code | opencode
      models:                        # provider/model, or inherit (the session's model)
        supervisor: anthropic/claude-haiku-5-5
        analyst: anthropic/claude-sonnet-5-5
        postdoc: anthropic/claude-opus-5-5   # runs only when asked (labpipe postdoc)

A model can also be set for one agent by name (models.xrd-analyst). Codex also takes a
reasoning effort per role (`reasoning: {supervisor: low, analyst: high, postdoc: high}`). Codex
agents are TOML files (.codex/agents/<name>.toml: name, description, model,
model_reasoning_effort, developer_instructions). The
generated files are git-ignored; each carries a fingerprint so `labpipe doctor`
can tell when they are out of date.

Lab overlays (docs/settings/agents.md): in the project, lab/agents/<role>.md is appended to
that role's instructions; a lab/agents/<new>.md with full frontmatter is an extra role. An
overlay that names no role is reported (`problems`), never dropped silently. In a project
(not dev mode) the code-side paths the instructions name (docs/…, methods.yaml, the default
config files, references/, agents/) point to the copies in the project's .labpipe/.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from labpipe.registry import load_methods
from labpipe.safety import (CODE_ROOT, LAB_CONFIG_FILES, PROJECT_ROOT, atomic_write_text,
                            is_dev)

ROLES_DIR = CODE_ROOT / "agents"
HARNESS_DIRS = {"claude-code": Path(".claude/agents"), "opencode": Path(".opencode/agents"),
                "codex": Path(".codex/agents")}
HARNESSES = {name: PROJECT_ROOT / folder for name, folder in HARNESS_DIRS.items()}
LAB_ROLES = "lab/agents"           # in the project: overlays and extra roles
CODE_COPY = ".labpipe"             # in the project: the code-side files agents may read
REQUIRED_KEYS = ("name", "role", "description")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
EXTENSION = {"codex": ".toml"}
REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
CLAUDE_ALIASES = {"inherit", "sonnet", "opus", "haiku", "fable"}
_FINGERPRINT = re.compile(r"labpipe-agents-fingerprint: ([0-9a-f]{16})")


class AgentConfigError(ValueError):
    pass


def codex_trust(project: Path = PROJECT_ROOT) -> str | None:
    """trust_level of this project in the user's Codex config (None if not set). Codex
    applies the project's .codex/ (rules, hooks, agents) only when it is "trusted"."""
    import os
    import tomllib
    config = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    try:
        projects = tomllib.loads(config.read_text(encoding="utf-8")).get("projects") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return None
    for path, entry in projects.items():
        try:
            if Path(path).resolve() == Path(project).resolve():
                return (entry or {}).get("trust_level")
        except OSError:
            continue
    return None


def _code_path(path: str) -> bool:
    """Is a path named in the instructions a code-side file (not one of the lab's)?"""
    if path.startswith(("docs/", "references/", "agents/")) or path == "methods.yaml":
        return True
    name = path.removeprefix("config/")
    return (path.startswith("config/") and "/" not in name and name.endswith(".yaml")
            and name not in LAB_CONFIG_FILES)


def readable_code_paths(text: str) -> str:
    """In a project, point backticked code-side paths to the copy in `.labpipe/`."""
    return _BACKTICKED.sub(lambda m: f"`{CODE_COPY}/{m.group(1)}`" if _code_path(m.group(1))
                           else m.group(0), text)


def _split(text: str) -> tuple[dict[str, Any] | None, str]:
    """(frontmatter, body) of a role file; frontmatter None when there is none."""
    if not text.startswith("---"):
        return None, text
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    return (meta if isinstance(meta, dict) else None), body.lstrip("\n")


def load_roles(problems: list[str] | None = None,
               project: Path | None = None) -> list[dict[str, Any]]:
    """The roles of the code, with the lab's overlays; extra roles of the lab. What is wrong
    with a lab file goes to `problems` (if given)."""
    folder = Path(project or PROJECT_ROOT)
    project = not is_dev(folder)
    prefix = f"{CODE_COPY}/" if project else ""
    roles = []
    for path in sorted(ROLES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front)
        body = body.lstrip("\n")
        roles.append({**meta, "body": readable_code_paths(body) if project else body,
                      "source": path.name, "text": text,
                      "origin": f"{prefix}agents/{path.name}"})
    by_name = {role["name"]: role for role in roles}
    lab = folder / LAB_ROLES
    for path in sorted(lab.glob("*.md")) if lab.is_dir() else []:
        if path.name.lower() == "readme.md":
            continue
        text = path.read_text(encoding="utf-8")
        label = f"{LAB_ROLES}/{path.name}"
        try:
            meta, body = _split(text)
        except (ValueError, yaml.YAMLError) as exc:
            if problems is not None:
                problems.append(f"{label}: unreadable frontmatter ({exc})")
            continue
        name = (meta or {}).get("name") or path.stem
        if name in by_name:      # an overlay: appended to that role (its frontmatter is ignored)
            role = by_name[name]
            role["body"] = (role["body"].rstrip("\n") + f"\n\n## This lab's additions ({label})"
                            f"\n\n{body.strip()}\n")
            role["text"] += f"\n--- overlay {label}\n{text}"
            role["origin"] += f" + {label}"
        elif meta is None:
            if problems is not None:
                problems.append(f"{label} names the role '{name}', which does not exist (roles: "
                                f"{', '.join(sorted(by_name))}); to add a role, start the file "
                                "with frontmatter (name, role, description, capabilities)")
        elif any(key not in meta for key in REQUIRED_KEYS):
            if problems is not None:
                problems.append(f"{label}: an extra role needs {', '.join(REQUIRED_KEYS)} in "
                                "its frontmatter")
        else:
            roles.append({**meta, "body": body, "source": path.name, "text": text,
                          "origin": label})
    return roles


def settings(config: dict) -> dict[str, Any] | None:
    agents = config.get("agents")
    if not agents:
        return None
    harness = agents.get("harness")
    if harness not in HARNESSES:
        raise AgentConfigError(f"agents.harness must be one of {sorted(HARNESSES)}, not {harness!r}")
    reasoning = dict(agents.get("reasoning") or {})
    bad = {k: v for k, v in reasoning.items() if v not in REASONING_EFFORTS}
    if bad:
        raise AgentConfigError(f"agents.reasoning must be one of {sorted(REASONING_EFFORTS)}: {bad}")
    return {"harness": harness, "models": dict(agents.get("models") or {}), "reasoning": reasoning}


def _model(role: dict, models: dict) -> str:
    return str(models.get(role["name"]) or models.get(role["role"]) or "inherit")


def _delegates(role: dict) -> list[str]:
    if role.get("delegates_to") == "method-analysts":
        return sorted({spec["subagent"] for spec in load_methods().values() if spec.get("subagent")})
    return list(role.get("delegates_to") or [])


def _extras(role: dict, harness: str) -> dict[str, Any]:
    """Harness-only frontmatter, copied as is (e.g. claude-code: {maxTurns: 40, hooks: ...})."""
    return dict((role.get("harness_extras") or {}).get(harness) or {})


def _claude_model(model: str) -> str:
    if model in CLAUDE_ALIASES:
        return model
    provider, _, name = model.partition("/")
    if provider == "anthropic" and name:
        return name
    raise AgentConfigError(f"Claude Code runs Anthropic models: use anthropic/<model>, "
                           f"an alias ({', '.join(sorted(CLAUDE_ALIASES))}), not {model!r}")


def _claude_code(role: dict, model: str, delegates: list[str]) -> str:
    tools = {"shell": "Bash", "read": "Read", "write": "Write"}
    names = [tools[c] for c in role.get("capabilities", []) if c in tools]
    if delegates:
        names.append(f"Agent({', '.join(delegates)})")
    front = {"name": role["name"], "description": role["description"],
             "tools": ", ".join(names), "model": _claude_model(model)}
    front |= _extras(role, "claude-code")
    return "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000) + "---\n"


def _opencode(role: dict, model: str, delegates: list[str]) -> str:
    capabilities = set(role.get("capabilities", []))
    permission: dict[str, Any] = {
        "edit": "allow" if "write" in capabilities else "deny",
        "webfetch": "deny",
        "bash": {"*": "deny", "labpipe *": "allow"} if "shell" in capabilities else "deny",
        "task": {"*": "deny", **{name: "allow" for name in delegates}},
    }
    front: dict[str, Any] = {"description": role["description"],
                             "mode": "all" if role["role"] == "supervisor" else "subagent"}
    if model != "inherit":
        if "/" not in model:
            raise AgentConfigError(f"OpenCode models are provider/model, not {model!r}")
        front["model"] = model
    front["permission"] = permission
    front |= _extras(role, "opencode")
    return "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000) + "---\n"


def _origin(role: dict) -> str:
    return role.get("origin") or f"agents/{role['source']}"


def _toml_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)   # a JSON string is a valid TOML basic string


def _codex(role: dict, model: str, effort: str | None, delegates: list[str],
           fingerprint: str) -> str:
    lines = [f"# Generated by `labpipe agents sync` from {_origin(role)}: edit that file, "
             f"not this one. labpipe-agents-fingerprint: {fingerprint}",
             f"name = {_toml_string(role['name'])}",
             f"description = {_toml_string(role['description'])}"]
    if model != "inherit":
        provider, _, name = model.partition("/")
        lines.append(f"model = {_toml_string(name if provider == 'openai' and name else model)}")
    if effort:
        lines.append(f"model_reasoning_effort = {_toml_string(effort)}")
    for key, value in _extras(role, "codex").items():
        lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    body = role["body"]
    if delegates:
        body += ("\n\nAgents you may delegate to (spawn them by name): "
                 + ", ".join(delegates) + ".\n")
    escaped = body.replace("\\", "\\\\").replace('"""', '\\"""')
    lines.append(f'developer_instructions = """\n{escaped}"""')
    return "\n".join(lines) + "\n"


def _fingerprint(role: dict, harness: str, model: str, delegates: list[str],
                 effort: str | None = None) -> str:
    material = "\n".join([role["text"], role["body"], harness, model, ",".join(delegates),
                          effort or ""])
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _effort(role: dict, reasoning: dict | None) -> str | None:
    reasoning = reasoning or {}
    return reasoning.get(role["name"]) or reasoning.get(role["role"])


def _render(role: dict, harness: str, models: dict,
            reasoning: dict | None = None) -> tuple[str, str]:
    model, delegates = _model(role, models), _delegates(role)
    if harness == "codex":
        effort = _effort(role, reasoning)
        fingerprint = _fingerprint(role, harness, model, delegates, effort)
        return _codex(role, model, effort, delegates, fingerprint), fingerprint
    head = (_claude_code if harness == "claude-code" else _opencode)(role, model, delegates)
    fingerprint = _fingerprint(role, harness, model, delegates)
    note = (f"<!-- Generated by `labpipe agents sync` from {_origin(role)}: edit that "
            f"file, not this one. labpipe-agents-fingerprint: {fingerprint} -->\n\n")
    return head + note + role["body"], fingerprint


def sync(config: dict, project: Path | None = None) -> dict[str, Any]:
    """Write the harness's agent files (into `project`, default: the current project)."""
    chosen = settings(config)
    if chosen is None:
        raise AgentConfigError("no `agents:` section in config/local.yaml (harness and models "
                               "per role); see config/local.example.yaml")
    project = Path(project or PROJECT_ROOT)
    target = project / HARNESS_DIRS[chosen["harness"]]
    written, problems = [], []
    for role in load_roles(problems, project):
        text, _ = _render(role, chosen["harness"], chosen["models"], chosen["reasoning"])
        path = target / f"{role['name']}{EXTENSION.get(chosen['harness'], '.md')}"
        atomic_write_text(path, text, config, pipeline=False)
        written.append({"agent": role["name"], "role": role["role"],
                        "model": _model(role, chosen["models"]),
                        "file": path.relative_to(project).as_posix()})
    return {"harness": chosen["harness"], "written": written, "problems": problems}


def status(config: dict) -> dict[str, Any]:
    """For doctor: configured? generated files present and current?"""
    try:
        chosen = settings(config)
    except AgentConfigError as exc:
        return {"configured": False, "problem": str(exc)}
    if chosen is None:
        return {"configured": False, "problem": "no agents section in config/local.yaml"}
    outdated, problems = [], []
    for role in load_roles(problems):
        try:
            _, expected = _render(role, chosen["harness"], chosen["models"], chosen["reasoning"])
        except AgentConfigError as exc:
            return {"configured": False, "problem": str(exc)}
        path = (HARNESSES[chosen["harness"]]
                / f"{role['name']}{EXTENSION.get(chosen['harness'], '.md')}")
        found = _FINGERPRINT.search(path.read_text(encoding="utf-8")) if path.exists() else None
        if not found or found.group(1) != expected:
            outdated.append(role["name"])
    return {"configured": True, "harness": chosen["harness"], "outdated": outdated,
            "problems": problems,
            "models": {role["name"]: _model(role, chosen["models"]) for role in load_roles()}}
