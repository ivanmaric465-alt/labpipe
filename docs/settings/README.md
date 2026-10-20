# Settings: what you can change

Your changes go in your project's own files (`config/`, `lab/`; never committed); labpipe's defaults come with the code and stay as shipped, so updates never conflict. Every settings file used is recorded in each result's `provenance.json`.

| I want to change … | Page | File |
|---|---|---|
| where my data is, which files are read or ignored, sample IDs | [data.md](data.md) | `config/local.yaml`, `config/ignore.yaml`, `config/sample_ids.yaml` |
| settings for a group of samples or one sample, nominal composition | [samples.md](samples.md) | `config/samples.yaml` |
| how a method analyses (background, peaks, phases, standards, …) | `docs/methods/<method>.md` → Settings | `config/lab.yaml` → `<method>`, `samples.yaml`, `config/measurements/` |
| the look of the plots | [plots.md](plots.md) | `config/lab.yaml` → `plots`, `<method>.plot` |
| the report layout, extra reports | [report.md](report.md) | `config/lab.yaml` → `report` |
| the project: its folder, conventions for all agents, a role of our own | [project.md](project.md) | `labpipe.yaml`, `lab/LAB.md`, `lab/agents/` |
| the agent tool and the models | [agents.md](agents.md) | `config/local.yaml` → `agents` |
| automatic processing, notifications, pop-ups | [automation.md](automation.md) | `config/local.yaml` → `automation` |
| what the postdoc knows about the project | [context.md](context.md) | `context/` |
| the container (port, engine, allowed hosts) | [container.md](container.md) | `config/local.yaml` → `container` |
| settings that an update renamed or moved (old names, backup) | [migrations.md](migrations.md) | `labpipe migrate` |
| updating labpipe: what changes, the preview, rollback, backups | [updates.md](updates.md) | `labpipe update` |

`labpipe update` ([updates.md](updates.md)) shows the settings files whose names a release changed, with the diff, and changes them only after you agree; `labpipe migrate` ([migrations.md](migrations.md)) does the same on its own. Old names keep working, with a warning, for one release.

Easiest: tell your agent what you want in your own words ("bigger fonts", "group the report by method"); it finds the setting on these pages, changes it with your OK and shows you the result.

Where a setting applies, later wins:

```
config/<method>.yaml, plots.yaml, report.yaml   labpipe's defaults (the code's; in a project read-only copies in .labpipe/config/)
← config/lab.yaml                               your lab's defaults
← config/samples.yaml                           presets, then one sample
← config/measurements/<method>/<id>.yaml        one measurement
```
