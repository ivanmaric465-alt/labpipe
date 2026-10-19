# Agents: tool and models

labpipe's numbers come from code; AI agents run the commands, look at plots and write text. You choose the agent tool and the model of each role.

| Role | What it does | Runs | Recommended (Claude Code / Codex) |
|---|---|---|---|
| supervisor | runs the pipeline commands, hands measurements to the analysts, keeps the books; no science | every automatic pass | Haiku / small, low reasoning |
| method analysts | look at one measurement's plot and results; write and check short notes | every new measurement | Sonnet / standard, medium reasoning |
| postdoc | answers your questions about the whole project, with its context | only when you ask | Opus / strongest, high reasoning |

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

## After a change

`labpipe agents sync`. Nothing is re-analysed; new notes are written with the new model.
