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

A model can also be set for one agent by name (models.xrd-analyst). Codex also takes a
reasoning effort per role (`reasoning: {supervisor: low, analyst: high}`). Codex
agents are TOML files (.codex/agents/<name>.toml: name, description, model,
model_reasoning_effort, developer_instructions). The
generated files are git-ignored; each carries a fingerprint so `labpipe doctor`
can tell when they are out of date.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from labpipe.registry import load_methods
from labpipe.safety import PROJECT_ROOT, atomic_write_text

ROLES_DIR = PROJECT_ROOT / "agents"
HARNESSES = {"claude-code": PROJECT_ROOT / ".claude" / "agents",
             "opencode": PROJECT_ROOT / ".opencode" / "agents",
             "codex": PROJECT_ROOT / ".codex" / "agents"}
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


def load_roles() -> list[dict[str, Any]]:
    roles = []
    for path in sorted(ROLES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front)
        roles.append({**meta, "body": body.lstrip("\n"), "source": path.name, "text": text})
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


def _toml_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)   # a JSON string is a valid TOML basic string


def _codex(role: dict, model: str, effort: str | None, delegates: list[str],
           fingerprint: str) -> str:
    lines = [f"# Generated by `labpipe agents sync` from agents/{role['source']}: edit that file, "
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
    material = "\n".join([role["text"], harness, model, ",".join(delegates), effort or ""])
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
    note = (f"<!-- Generated by `labpipe agents sync` from agents/{role['source']}: edit that "
            f"file, not this one. labpipe-agents-fingerprint: {fingerprint} -->\n\n")
    return head + note + role["body"], fingerprint


def sync(config: dict) -> dict[str, Any]:
    chosen = settings(config)
    if chosen is None:
        raise AgentConfigError("no `agents:` section in config/local.yaml (harness and models "
                               "per role); see config/local.example.yaml")
    target = HARNESSES[chosen["harness"]]
    written = []
    for role in load_roles():
        text, _ = _render(role, chosen["harness"], chosen["models"], chosen["reasoning"])
        path = target / f"{role['name']}{EXTENSION.get(chosen['harness'], '.md')}"
        atomic_write_text(path, text, config, pipeline=False)
        written.append({"agent": role["name"], "role": role["role"],
                        "model": _model(role, chosen["models"]),
                        "file": str(path.relative_to(PROJECT_ROOT))})
    return {"harness": chosen["harness"], "written": written}


def status(config: dict) -> dict[str, Any]:
    """For doctor: configured? generated files present and current?"""
    try:
        chosen = settings(config)
    except AgentConfigError as exc:
        return {"configured": False, "problem": str(exc)}
    if chosen is None:
        return {"configured": False, "problem": "no agents section in config/local.yaml"}
    outdated = []
    for role in load_roles():
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
            "models": {role["name"]: _model(role, chosen["models"]) for role in load_roles()}}
