# The postdoc: overall analysis on request (design, draft)

Status: draft, awaiting approval. Nothing is implemented yet.

## Goal

The method analysts look at one measurement at a time. The **postdoc** is an agent that looks
at the whole dataset of a project:
- across samples and methods;
- with the project's context (aims, samples, work plan, literature);
- answering a question the user asks.

It runs only on request, never in automatic runs, with a model the user chooses (setup
recommends a strong one). The result goes to the report. When there is none, when it is out of
date, or when it failed its checks, the report says so.

## The context folder (per project, git-ignored, never committed)

```
context/
  context.md        the project in plain words: aims, hypotheses, the samples and what they
                    should show, what matters. Written by the user, or by the agent with the user.
  instructions.md   how the postdoc uses context.md and the documents: which document is for
                    what, what to prioritise, what to leave out. Created from a template, edited
                    by the user.
  documents/        project files, the work plan, literature: PDF, DOCX, Markdown, text
  .text/            plain-text copies of documents/, with page markers (made by labpipe)
```

- **`labpipe context init`** creates the folder, `context.md` and `instructions.md` from
  `templates/context/`. It never overwrites an existing file.
- **`labpipe context index`** extracts the text of `documents/` into `.text/`, and records each
  document's sha256:
  - PDFs with `pdftotext` (poppler-utils, in the container image), keeping page numbers so
    citations can say "work-plan.pdf p. 4";
  - DOCX with the Python standard library.

  Every harness can then read the documents, and the checks can find numbers taken from the
  literature.
- **Writing `context.md` with the agent**: the user says *"let's write the project context"*.
  The main session then:
  1. asks about the project;
  2. reads the documents;
  3. drafts `context.md` and shows it.

  The user corrects the draft. The file is written only with their approval, and so are
  later updates.
- **`instructions.md`** cannot loosen the rules below. It only steers what is read, how the
  documents are weighed and what to focus on. Example lines from the template:
  - the work plan defines the aims;
  - papers are for background and reference values only;
  - `context.md` wins over older documents;
  - answer in English.
- **Agents never change `documents/`.** It is read-only, like raw data, and the guard
  enforces it.
- **Privacy**: whatever is in `context/` is sent to the model provider (Anthropic or OpenAI)
  when an agent reads it, as results and plots are now.

## Several projects

- One project is one folder with its own:
  - data folder;
  - settings;
  - context;
  - analyses;
  - container (its own name, port and home volume).
- An agent on one project sees only that project's files, context and data. It sees no other
  project's context, and no harness sessions or memory from another project.
- Separating the code (installed once) from project folders is part of the installation step.
  The postdoc uses only paths inside the project folder, so it works either way.

## Comparing across samples: named quantities

Results differ by method, so a comparison cannot just take any key from `results.json`:
- a key can mean different things in different files (e.g. `phases.fcc_indexed[0]` can be a
  different phase in two samples);
- some methods have no numbers at all (SEM is images only).

Each method therefore declares the **quantities** that are meaningful to compare, in its
summary module (`quantities(results)`). Each quantity has a name, value, σ, unit, and the key
path it comes from. The method's doc lists them under a new heading "Quantities". Examples:

| Method | Quantity | From |
|---|---|---|
| xrd | `xrd.a.<phase>` lattice parameter (Å, σ) | the fcc phase with that ID, not a list position |
| xrd | `xrd.size.<phase>` crystallite size (nm, lower bound) | sizing |
| eds | `eds.at.<element>` metal-basis composition (at%) | `metal_basis.at_percent` |
| eds | `eds.at_minus_nominal.<element>` (at% points) | `nominal_comparison` |
| sem | none | images only; the postdoc may look at them, but makes no quantitative claims |
| echem (later) | e.g. `echem.eta_10` overpotential at 10 mA cm⁻² (V), `echem.tafel` slope (mV dec⁻¹) | that method's results |

Sample properties from `config/samples.yaml` are quantities too (e.g. `sample.nominal.Pt`).

**`labpipe dataset`** prints one table for the whole project:
- one row per sample;
- its sample properties;
- every quantity of every analysed measurement, with σ;
- each measurement's status, `review` checks and the paths to its notes and plot.

A sample with several measurements of one method gets one line per measurement. Nothing is
averaged without being asked.

**`labpipe dataset compare`** does the arithmetic. Each call is saved with the run as
`compare-N.json`:
- `--quantity Q --reference SAMPLE`: each sample's difference from a reference sample, with σ;
- `--quantity Q --against P`: a linear trend of Q against another quantity P. This works
  within a method or across methods: XRD lattice parameter against measured EDS Pt at%, or
  against nominal Pt. It gives the slope, intercept, their σ, R² and n, the points used and
  the samples left out, with the reason (not analysed, failed, flagged for review).

When a sample has several measurements, the user or the postdoc picks one by
`--measurement`, or chooses `--each` (every measurement as its own point). The tool never
picks silently.

For a new method, declaring its quantities is part of adding the method
(`docs/ADDING_A_METHOD.md`). A method without quantities can still be read by the postdoc
(notes, plots), but not compared by code. Other operations (ratios, averages over repeats) are
code changes, added with tests when needed.

## A run

All files of one request go in `analysis/_postdoc/<UTC time>/`, which is never deleted:

| File | Written by | Content |
|---|---|---|
| `request.md` | `labpipe postdoc start` | question, date, harness, model |
| `inputs.json` | `labpipe postdoc start` | sha256 of `context.md`, `instructions.md`, each document, each `results.json` and `notes.md`, the sample settings; labpipe version |
| `dataset.json` | `labpipe postdoc start` | the `labpipe dataset` table at that moment |
| `compare-N.json` | `labpipe dataset compare --run RUN` | each comparison asked for |
| `analysis.md` | the postdoc | the analysis |
| `verify.json` | `labpipe postdoc verify` | result of the checks |

`analysis.md` is the postdoc's own text. Comparisons are a tool it may use, not the job:
it writes freely about what it sees. That includes insights the user may not have looked
for:
- patterns across samples and methods;
- contradictions between measurements, or with the context;
- suspicious or surprising results;
- alternative explanations;
- what would settle an open question.

Three parts are required, so every analysis can be read and trusted the same way:
- **Question**, copied from the request, at the top;
- **Summary**: a few sentences;
- **Check before using**: every `review` flag of the measurements it cites, plus failed,
  stale or missing measurements that matter for the question.

Everything else (headings, length, order) is the postdoc's choice. Two kinds of statements
must look different:
- **observation**: what the data show;
- **interpretation or idea**: says how certain it is (high, medium, low, speculative) and
  which context or literature it leans on (`documents/paper.pdf p. 6`).

`context/instructions.md` can ask for a structure or a focus; the template suggests one, e.g.
"insights and ideas" and "suggested next steps".

**`labpipe postdoc verify RUN`** checks only what keeps the text honest, not its content:
- every number in `analysis.md` is in `dataset.json`, a `compare-N.json`, or the
  `results.json` of a measurement the text names. Free text without numbers is never
  restricted;
- a number from the literature appears in that document's text, on a line that cites it;
- every `review` check of a measurement the text names is mentioned;
- the three required parts are present.

It reuses the number matching of `labpipe notes verify`. The postdoc may fix `analysis.md`
twice; after that the run is recorded as failed, with the reasons.

## How the user asks for it

**In a conversation** (the usual way): *"ask the postdoc: <question>"*.
1. The main session runs `labpipe postdoc start "<question>"`.
2. It hands the run to the `postdoc` agent.
3. It runs `labpipe postdoc verify` and then `labpipe report`.
4. It shows the summary and the report link.

**Without a conversation**: `labpipe postdoc run "<question>" [--budget-usd N]`.
- It runs inside the container like the automatic runs, with a cost limit (Claude: USD;
  Codex: a token limit).
- A notification says when it is ready, or why it failed.

**`labpipe postdoc list`** shows past runs: their questions, whether they were verified, and
whether they are up to date. Several questions are separate runs.

## The postdoc agent

- **Role file**: `agents/postdoc.md`. It reads files and runs `labpipe` commands. It writes
  only its run's `analysis.md`. It does not start other agents.
- **Model**: `config/local.yaml` → `agents.models.postdoc`, plus
  `agents.reasoning.postdoc` for Codex.
- **Its instructions**:
  - the AGENTS.md rules;
  - the required parts and the citation format above; otherwise free, and expected to point
    out what the user may not have noticed;
  - interpretation only from the results and the context, worded as each method's doc says
    ("consistent with", never "is");
  - "the data cannot answer this" rather than stretching;
  - a measurement it needs that is not analysed is listed under "Check before using", never
    analysed by the postdoc itself.

## Choosing the models (setup asks)

`labpipe setup`, and the one-command installer later, asks for one model per role. It first
says in a sentence what each role does:

| Role | What it does | Needs | Recommended (Claude Code) | Recommended (Codex) |
|---|---|---|---|---|
| supervisor | runs the pipeline commands, hands measurements to the analysts, keeps the books. No science. | following steps reliably; runs often | Haiku | small model, low reasoning |
| method analysts | look at one measurement's plot and results; write and verify short notes | reading plots, careful wording | Sonnet | standard model, medium reasoning |
| postdoc | reads the whole dataset, your context and literature; answers your question | reasoning across many inputs; runs only when you ask | Opus | strongest model, high reasoning |

- **Cost** is said in plain words: the supervisor and analysts run on every new
  measurement, the postdoc only on request (one run can cost more than a whole batch of
  notes).
- **Pressing Enter takes the recommendation.** `inherit` (the harness default) stays possible.
- **Where the recommendations live**: per harness in `config/pipeline.yaml` →
  `agents.recommended`. When models change, one file is updated and the code stays as it is.
- **The answers** go to `config/local.yaml` → `agents`, followed by `labpipe agents sync`.

## In the report

A new first section, **The postdoc's analysis**:
- **None yet**: *"The postdoc has not been asked anything yet. To ask, open Claude Code in
  /home/…/project and say: ask the postdoc: <your question>"*. The harness and folder are the
  user's, as in the notifications.
- **Latest run**: its question, date and model; `analysis.md` rendered, with the
  measurements it cites linked to their sample cards.
- **Out of date**: a banner listing what changed, found by comparing `inputs.json` with the
  files now. For example: *"Out of date: 3 results changed, 2 new measurements (S-18 xrd,
  S-18 eds), context.md changed. Ask again to update."*
- **Failed checks**: *"The postdoc's analysis of <date> did not pass its checks: <reasons>"*,
  followed by the last one that passed, if any.
- **Earlier runs**: collapsed, each with its question.

Automatic runs never start the postdoc. They only rebuild the report, which then shows the
banner.

## Changes to existing files

- **AGENTS.md**:
  - new commands: `context init|index`, `dataset`, `dataset compare`,
    `postdoc start|verify|run|list`;
  - the postdoc in the agents table;
  - a short section "Project context and the postdoc": what the user says and what to do,
    pointing to `agents/postdoc.md` and `context/instructions.md`;
  - rule 1 extended: `context/documents/` is read-only, like raw data.
- **docs/SETUP.md**: the model item uses the table above; a new optional last item,
  "Project context".
- **docs/methods/xrd.md, eds.md, sem.md, template**: a "Quantities" heading.
  `docs/ADDING_A_METHOD.md`: declare the quantities.
- **Guard and harness permissions**:
  - `context/documents/**` read-only for everyone;
  - `context/context.md` and `instructions.md` edited only with the user's approval;
  - the postdoc writes only its run's `analysis.md`.
- **`.gitignore`**: `/context/`.
- **Container image**: `poppler-utils`, for `pdftotext` (approved).
- **Tests**:
  - quantities for xrd and eds against fixtures;
  - the dataset table;
  - the differences and the trend, checked against hand-computed values;
  - verify accepting good text and rejecting invented or uncited numbers;
  - the report in each of its four states;
  - the guard refusing writes to `documents/`;
  - setup writing the chosen models.
