# The first conversation (for agents)

Setup comes first: `labpipe doctor` says `ready: true`. Onboarding follows when doctor shows the `onboarding` item (a warning, it never blocks), or when the user says "start", "tour" or "show me around". Lead the user through the steps below **one at a time**: short, in plain words, with the user's own data (sample IDs like S-9, never invented ones). After each step run `labpipe onboarding done <id>`. It resumes where it stopped.

- `labpipe onboarding` shows the steps and the next one, with its section of this page.
- The user can skip any step (`labpipe onboarding skip <id>`), or everything: "skip the onboarding" is `labpipe onboarding skip all`. Never push a step the user does not want.
- "tour" (the user says it) repeats the tour: `labpipe onboarding restart --tour`, then the `tour-*` steps. `labpipe onboarding restart` starts all over.
- Name the user's actual agent tool (the one they are talking to you in) wherever a message would name one.
- Numbers: copy them from `labpipe <method> summary` or `results.json`; never compute (AGENTS.md → Rules).

## `welcome`

Say these five things in your own words, in about five lines:

1. Your files are only read, never changed.
2. The analysis is plain code: every number can be traced to its input and settings, and regenerated (provenance, `labpipe replay`).
3. AI writes the notes and answers questions; it never calculates.
4. You steer in your own words ("bigger fonts", "fit this peak differently").
5. The report is where you read everything.

Then, in one or two sentences, the three folders (README → Three folders): the **code** (labpipe, replaced by updates), **your data** (only read), and the **project** (your settings, context and every result; where you open this tool). Ask if they want to go on, then `labpipe onboarding done welcome`.

## `files`

1. `labpipe inventory`. Show, in plain words: per method the files it recognised (counts, two examples), file types no method reads.
2. Sample IDs: show how the names map to sample IDs (doctor's `sample_ids`); fix the rule with the user if it is wrong (`docs/SETUP.md` → Sample IDs).
3. Files to leave alone (reports, photos, slides): with the user's OK, add them to `config/ignore.yaml` (AGENTS.md → Recognising the user's files).

Rerun `labpipe inventory` until the user says it is right, then `labpipe onboarding done files`.

## `methods`

For each method found in their data: ask its setup questions, `docs/methods/<method>.md` → Setup (e.g. XRD: which reference phases, internal standard and its certificate value). Answers go into settings, never code. For the lab's own methods: its `lab/methods/<method>/README.md`. Only ask what matters for this data; skip what the user does not know yet. `labpipe onboarding done methods`.

## `first-results`

Per method, one measurement the user picks (suggest one):

1. `labpipe scan`, then `labpipe <method> analyze --entry ID`. Code does this, not you.
2. Show `plot.png`. Explain it with the method page's **Reading the plot** section (`docs/methods/<method>.md`): three to five bullets, what each line, panel and colour is.
3. The key numbers (`labpipe <method> summary --entry ID`) and the checks, in plain words; what each flag means and what to do about it.
4. The notes: delegate to the method's analyst as usual (AGENTS.md → Agents), then `labpipe notes verify --entry ID`, and show them.

Then `labpipe onboarding done first-results`.

## `tour-report`

The tour is about one minute per part, and each part can be skipped. First: the report. `labpipe report`, point the user to `analysis/report.html` (open it in a browser; it is rebuilt after every pass). Show what is on it (a comparison of the samples, overview tables, one card per measurement) and the layouts: group by sample or method, full or compact cards (`docs/settings/report.md`). `labpipe onboarding done tour-report`.

## `tour-steering`

One change on their own plot, only with the user's OK. Ask what they would change ("bigger fonts", "a different x range"); find it under "You say → setting" on the page of the plot or method; change it (AGENTS.md → Steering an analysis), rerun (`labpipe replot --entry ID` for plot-only changes), and show the result. Say that the old result is kept in `_superseded/` and the change is recorded. `labpipe onboarding done tour-steering`.

## `tour-agents`

The roles and their models (`docs/settings/agents.md`): the supervisor (runs commands, small model), the analysts (write the notes, standard model), the postdoc (answers questions about the whole project, strongest model, only when asked). Say which model each uses now (`labpipe doctor`, item `agents`), and that they change it by saying so. `labpipe onboarding done tour-agents`.

## `tour-automation`

Automatic processing (`docs/settings/automation.md`): new files are noticed, analysed by code, the notes written in batches, the report rebuilt; the AI starts only when there is work. The notifications page and pop-ups say what needs the user. Say what is on now (`labpipe automation status`). The user sets it up or changes it (`labpipe automation setup` in their own terminal for native mode; `labpipe container start` otherwise); do not run it. `labpipe onboarding done tour-automation`.

## `tour-postdoc`

Optional. The postdoc answers questions about the whole project ("which samples changed most?", "ask the postdoc: …"). It reads `context/` (aims, documents; `docs/settings/context.md`). Offer `labpipe context init` and write `context.md` together only if the user wants it now. `labpipe onboarding done tour-postdoc`.

## `tour-settings`

Where the settings live: `docs/settings/README.md` lists a page per topic, each with a "You say → setting" table. Say that they never need to find a file: they say what they want, you find the page and change it with their OK. `labpipe onboarding done tour-settings`.

## `tour-updates`

`labpipe update` brings a new release: it shows what changed (`CHANGELOG.md`), backs up settings, and lists what is out of date. **The user runs it** in their terminal; you show `labpipe update --check` when they ask or doctor shows `project_release` (`docs/settings/updates.md`). `labpipe onboarding done tour-updates`.

## `done`

One line on what to say next time: "process the new data", "check the review queue", "ask the postdoc: …", "tour" to see this again. `labpipe onboarding done done`.
