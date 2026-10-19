# Overall analysis (design, draft)

Status: draft, awaiting approval. Nothing is implemented yet.

## Goal

The method analysts look at one measurement at a time. The overall analysis looks at the
whole dataset of a project:
- across samples and methods;
- with the project's context (aims, samples, work plan, literature);
- answering a question the user asks.

It runs **only on request**, never in automatic runs, and with a strong reasoning model. The
result goes to the report. When there is none, or it is out of date, or it failed its checks,
the report says so.

## The context folder (per project, git-ignored, never committed)

```
context/
  context.md        the project in plain words: aims, hypotheses, the samples and what they
                    should show, what matters; written by the user, or by the agent with the user
  instructions.md   how the agent uses context.md and the documents (which document is for what,
                    what to prioritise, what to leave out); created from a template, edited by the user
  documents/        project files, the work plan, literature: PDF, DOCX, Markdown, text
  .text/            plain-text copies of documents/, with page markers (made by labpipe, below)
```

- `labpipe context init` creates the folder, `context.md` and `instructions.md` from
  `templates/context/`. It never overwrites an existing file.
- `labpipe context index` extracts the text of `documents/` into `.text/`:
  - PDFs with `pdftotext`, keeping page numbers, so citations can say "work-plan.pdf p. 4";
  - DOCX with the Python standard library (a DOCX is zipped XML).
  
  This means every harness can read the documents, not only those that open PDFs, and the
  checks can find numbers that come from the literature. It also records each document's sha256.
- **Writing `context.md` with the agent**: the user says *"let's write the project context"*.
  The main session asks about the project, reads the documents, drafts `context.md`, and shows
  it. The user corrects it; the file is written only with their approval. The same applies to
  later updates.
- `instructions.md` cannot loosen the rules below. It only steers what the agent reads, how it
  weighs the documents and what it focuses on. Example lines from the template:
  - the work plan defines the aims;
  - papers are for background and reference values only;
  - `context.md` wins over older documents;
  - answer in English.
- Agents never change anything in `documents/` (same rule as raw data; the guard enforces it).
- **Privacy**: whatever is in `context/` is sent to the model provider (Anthropic or OpenAI)
  when the agent reads it, like results and plots are now. Put nothing there that may not go
  to them.

## Several projects

- One project is one folder with its own:
  - data folder;
  - settings;
  - context;
  - analyses;
  - container (its own name, port and home volume).
- An agent working on one project sees only that project's files, its context and its data.
  It sees no other project's context, and no harness sessions or memory from another project.
- Today a project is a clone of the repository. The installation step will separate the code
  (installed once) from project folders (`labpipe project new <folder>`). The overall analysis
  only uses paths inside the project folder, so it works with either.

## What the agent gets

1. `context/instructions.md`, `context/context.md`, and `context/.text/*` as instructed.
2. The question, e.g. *"How does the lattice parameter follow the Pt content across S-9…S-17,
   and which samples should be measured again?"*
3. **`labpipe dataset`**: one table of the whole project, made by code.
   - One row per sample: the sample settings from `config/samples.yaml` (nominal
     composition, support, notes).
   - Per method: the key numbers from `labpipe <method> summary`, with σ, the checks, the
     status (done, failed, stale, missing) and the paths to `notes.md` and `plot.png`.
   - No arithmetic: values are copied from the results.
4. **`labpipe dataset compare`**: the comparisons that need arithmetic, done by code and saved
   with the run:
   - `--key <path> --reference <sample>`: the difference of each sample from a reference
     sample, with σ;
   - `--key <path> --by <numeric sample field>`: a linear trend (slope, intercept, σ, R², n)
     against e.g. a nominal Pt fraction, with the points used and the samples left out (and why).
   
   The agent may ask for any of these; it never calculates. Other operations are code
   changes, added with tests when needed.
5. The method notes and plots of the measurements it uses (read, never changed).

## A run

All files of one request go in one folder, `analysis/_overall/<UTC time>/`, which is never deleted:

| File | Written by | Content |
|---|---|---|
| `request.md` | `labpipe overall start` | question, date, harness and model |
| `inputs.json` | `labpipe overall start` | sha256 of `context.md`, `instructions.md`, each document, each `results.json` and `notes.md`, the sample settings; labpipe version |
| `dataset.json` | `labpipe overall start` | the `labpipe dataset` table at that moment |
| `compare-*.json` | `labpipe dataset compare --run <run>` | each comparison asked for |
| `overall.md` | the agent | the analysis |
| `verify.json` | `labpipe overall verify` | result of the checks |

`overall.md` has fixed sections:
1. **Question**: copied.
2. **Summary**: three to five sentences.
3. **What the data show**: findings. Each finding cites where its numbers come from
   (`S-9 xrd S-9_long_scan: phases.fcc_indexed[0].a_angstrom` or `compare-1.json`).
4. **Interpretation**: clearly separated from the findings. Each point says:
   - how certain it is (high / medium / low, and why);
   - which context or literature it leans on (`documents/paper.pdf p. 6`).
5. **Against the context**: where the data agree or disagree with the hypotheses in
   `context.md`, and what the data cannot answer.
6. **Check before using**:
   - every `review` flag of the measurements it used;
   - failed, stale or missing measurements that would matter for the question.
7. **Suggested next steps**: measurements or reruns, with the reason for each.

`labpipe overall verify <run>` passes only if:
- every number in `overall.md` is in `dataset.json`, a `compare-*.json`, or a cited
  `results.json`;
- a number from the literature appears in that document's text, on a line that cites it;
- every `review` check of a cited measurement is mentioned;
- the seven sections are present;
- `inputs.json` matches the files cited.

This reuses the number matching of `labpipe notes verify`. If verification fails, the agent
may fix `overall.md` twice; after that the run is recorded as failed, with the reasons.

## How the user asks for one

- **In a conversation** (the usual way): *"analyse the whole dataset: <question>"*.
  1. The main session runs `labpipe overall start "<question>"`.
  2. It hands the run to the `overall-analyst` agent.
  3. After the agent: `labpipe overall verify`, then `labpipe report`.
  4. It shows the summary and the link to the report.
- **Without a conversation**: `labpipe overall run "<question>" [--budget-usd N]`.
  - It runs inside the container like the automatic runs, with a cost limit (Claude: USD;
    Codex: a token limit).
  - A notification says when it is ready or why it failed.
- `labpipe overall list`: past runs, their questions, verified or not, up to date or not.

Several questions are separate runs. The report shows the latest one first, then the earlier
ones, each with its own question.

## The agent

- **New role `agents/overall-analyst.md`**: reads and runs `labpipe` commands; writes only
  `analysis/_overall/<run>/overall.md`.
- **Model**: `config/local.yaml` → `agents.models.overall` (and `agents.reasoning.overall` for
  Codex). `labpipe setup` suggests the strongest model of the chosen harness. It is costly, so
  it is used only on request.
- **Instructions in the role file**:
  - the AGENTS.md rules;
  - the sections and citation format above;
  - interpretation only from the results and the context, worded as each method's doc says
    ("consistent with", never "is");
  - say "the data cannot answer this" rather than stretch.
- **No delegation**: it does not start other agents.
- **Missing analyses**: if a measurement it needs is not analysed, it says so in "Check before
  using" and does not analyse it itself.

## In the report

A new first section, **Overall analysis**:
- **None yet**: *"No overall analysis has been requested yet. To ask for one, open Claude Code in
  /home/…/project and say: analyse the whole dataset: <your question>"* (the harness and folder
  are the user's, as in the notifications).
- **Latest run**:
  - its question, date and model;
  - `overall.md` rendered, with the figures it cites linked to their sample cards.
- **Out of date**: a banner listing what changed since, by comparing `inputs.json` with the
  files now. For example: *"Out of date: 3 results changed, 2 new measurements (S-18 xrd, S-18
  eds), context.md changed. Ask again to update."*
- **Failed checks**: *"The overall analysis of <date> did not pass its checks: <reasons>"*,
  followed by the last one that passed, if any.
- **Earlier runs**: collapsed, with their questions.

The automatic runs never start an overall analysis. They only rebuild the report, which then
shows the banner.

## Changes to existing files

- **AGENTS.md**:
  - the commands (`context init|index`, `dataset`, `dataset compare`, `overall start|verify|run|list`);
  - the new agent in the table;
  - a short section "Project context and the overall analysis": what the user says, what to do,
    pointing to `agents/overall-analyst.md` and `context/instructions.md`;
  - rule 1 extended: `context/documents/` is read-only like raw data.
- **docs/SETUP.md**: an optional last item, "Project context": `labpipe context init`, put the
  documents in, write `context.md` together.
- **Guard and harness permissions**:
  - `context/documents/**` read-only for everyone;
  - `context/context.md` and `instructions.md` edited only with the user's approval;
  - the overall analyst writes only its run's `overall.md`.
- **`.gitignore`**: `/context/`.
- **Container image**: `poppler-utils` (for `pdftotext`; a system package, not a Python
  dependency). **This is a new dependency.**
- **Tests**:
  - the dataset table and comparisons (numbers against hand-checked fixtures);
  - verify accepting good and rejecting invented or uncited numbers;
  - the report showing each of the four states;
  - the guard refusing writes to `documents/`.

## Open questions

1. `labpipe dataset compare` with difference and linear trend in the first version, or the
   table only first?
2. Is `pdftotext` (poppler-utils, in the image) acceptable? Without it, only Markdown, text
   and DOCX documents can be used.
3. Should the default for the model be the strongest one of the harness (Claude: Opus, high
   effort), or should setup ask?
