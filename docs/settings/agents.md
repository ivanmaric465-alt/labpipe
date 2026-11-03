# Agents: tool and models

labpipe's numbers come from code; AI agents run the commands, look at plots and write text. You choose the agent tool and the model of each role.

| Role | What it does | Runs | Recommended (Claude Code / Codex) |
|---|---|---|---|
| supervisor | runs the pipeline commands, hands measurements to the analysts, keeps the books; no science | every automatic pass | Haiku / small, low reasoning |
| method analysts | look at one measurement's plot and results; write and check short notes | every new measurement | Sonnet / standard, medium reasoning |
| postdoc | answers your questions about the whole project, with its context | only when you ask | Opus / strongest, high reasoning |

## The tools

What labpipe can do with each tool (shown by `labpipe setup --only agents` and by the installer when you choose):

| Tool | Installed by labpipe | Container mode | Automatic runs | Guard hook | Cost cap per AI run | Models per role |
|---|---|---|---|---|---|---|
| Claude Code | container image: yes; on your computer: command shown, you run it | yes | yes | yes (.claude/settings.json); automatic runs: only labpipe commands and notes | yes, in USD (automation.max_budget_usd) | yes: haiku, sonnet, opus or anthropic/<model> |
| Codex | container image: yes; on your computer: command shown, you run it | yes (login: labpipe container login codex) | yes, if Codex trusts the project | yes (.codex/hooks.json): shell commands and patches; reads in strict forms only | no USD cap: tokens are logged, the batch size is the cap | yes, and a reasoning effort per role |
| OpenCode | no, you install it | not supported | not supported | no hook: only the read-only view and labpipe's own write checks | none (no automatic runs) | yes: provider/model; untested |

Safety layers per tool: [SAFETY.md](../SAFETY.md). Unattended runs and the cost cap: [automation.md](automation.md).

## Settings

`config/local.yaml`:

```yaml
agents:
  harness: claude-code       # claude-code | codex | opencode
  models:                    # inherit = the tool's default model
    supervisor: haiku
    analyst: sonnet
    postdoc: opus
  reasoning:                 # Codex only
    postdoc: high
```

A single agent can get its own model by name (`models.xrd-analyst`). The recommendations live in `config/pipeline.yaml` → `agents.recommended`.

```bash
labpipe setup --only agents   # asks, explains each role, Enter takes the recommendation
labpipe agents sync           # after editing by hand
```

## You say → setting

| You say | Setting |
|---|---|
| "I use Codex now" | `agents.harness: codex`, then `labpipe agents sync` |
| "the notes are too sloppy" | a stronger `models.analyst` |
| "automatic runs use too much of my plan" | a smaller `models.supervisor` / `analyst`; fewer `automation.max_measurements_per_run` |

## Instructions of your own

Roles come with labpipe. To add to one role or to add a role of your own, put a file in `lab/agents/` (the project page: [project.md](project.md)); `labpipe agents sync` appends it to the role. The analyst of a method of your own is such a role: `labpipe methods new` writes `lab/agents/<name>-analyst.md`.

## After a change

`labpipe agents sync` (in a project it runs `labpipe project sync`). Nothing is re-analysed; new notes are written with the new model.
