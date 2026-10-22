# Your own analyses in the report (design, draft)

Status: approved 2026-10-22 with the decisions below; being built (branch `own-analyses`).

The request: users have analyses already done elsewhere (an Origin fit exported as PNG and CSV,
a table from instrument software, a short write-up) and want them in the report next to
labpipe's own. They say which files are which: by folder, not only by extension (a `.png` can
belong to any analysis). labpipe then gives that kind of file its own analyst agent.

## How a user does it

Put the files in the data folder (read-only for labpipe, like every raw file), in a folder of
their own, e.g.:

```
Raman/fits/S-9_raman.png      the figure, as exported
Raman/fits/S-9_raman.csv      its table (optional)
Raman/fits/S-9_raman.md       how it was done, a remark (optional)
```

Then tell the agent ("include my Raman fits from Raman/fits"), or run:

```bash
labpipe methods import raman_fits --folder "Raman/fits" --files "*.png,*.csv,*.md" \
  --description "Raman D/G fits made in Origin"
labpipe scan
```

Files nobody claims already raise the "unrecognised files" notification; it will name this
command as one way to take them in.

## What `methods import` creates (never overwrites, all in the project)

- `lab/methods.yaml`: an entry `kind: imported` with the patterns
  `Raman/fits/**/*.png` etc. Folder plus extension decides; the shipped methods still come
  first, so a lab entry never takes an XRD file.
- `lab/methods/raman_fits/README.md`: **the description of the analysis**, a short form the
  user (or the agent, from what the user says) fills in: what was measured, with which software
  and model, what the columns mean and their units, what to look at in the figure. The agent
  reads it before every note; the report shows its first paragraph on the cards.
- `lab/methods/raman_fits/config.yaml`: optional settings (below).
- `lab/agents/raman_fits-analyst.md`: the agent, from a new template
  (`methods/template/imported-analyst.md`).

No Python is written: one shipped module (`labpipe.methods.imported`) handles every imported
kind.

## What labpipe does with the files (code, no AI)

- **One measurement per file stem in a folder**: `S-9_raman.png`, `.csv` and `.md` are one
  measurement. Sample: from the file name (the usual sample-ID rule), or from the folder name
  (`sample_from: folder`, for `Raman/fits/S-9/*.png`).
- `results.json` lists each file with its sha256 and what it is:
  - an image (png, jpg, tif): shown;
  - a table (csv, tsv, txt with columns): read as text and copied verbatim, at most 200 rows;
  - text (md, txt): shown as written;
  - a PDF: linked.

  Nothing is recalculated.
- **Optional named quantities**, e.g. `quantities: {raman.id_ig: {column: "ID/IG", unit: ""}}`
  in its config. A column becomes a quantity, so it shows up in `labpipe dataset`, the postdoc's
  data and the comparison figures. Values are copied from the table (σ from a named column, or
  none).
- **Checks:**
  - a table without units in its header;
  - a quantity column missing or not numeric;
  - no description in README.md;
  - a figure without a table (its numbers cannot be quoted).
- **The report card** gets a clear badge, "your analysis, made outside labpipe". It shows the
  figure(s), the table and the description.

## Provenance and replay: an exception to name explicitly

Rule: "every figure must be regenerable from provenance.json alone". An imported figure was
made outside labpipe, so labpipe cannot redraw it. Proposal:

- provenance.json records each file's path and sha256, and `"made_by": "outside labpipe"`;
- `labpipe replay` on it **checks** instead of redrawing: the file is still there and unchanged;
- the report and the notes say so. Comparison figures from its quantities are labpipe's and
  replay as usual.

## The agent (`<name>-analyst`)

It works like the other analysts and follows the same `AGENTS.md` rules and `docs/WRITING.md`.
It reads the README.md description, looks at the figure and reads the table through
`labpipe <name> summary`. Then it writes a short `notes.md`:

- what the analysis shows;
- numbers quoted only from the table, verified by `labpipe notes verify` as now;
- plot impressions marked "(plot)";
- what is missing for a reader: units, σ, how the fit was done.

It never redoes or "corrects" the user's analysis. `notes: false` in the entry turns it off,
leaving only the files in the report.

## Decisions (2026-10-22, user)

1. **The files live in the data folder**, like every measurement: the data folder holds what
   people made (instrument files and their own analyses, read-only for labpipe), the project
   folder holds what labpipe makes from them (`analysis/<sample>/<name>/`: results.json, the
   copies shown in the report, notes.md, provenance.json). So the split stays the same as for
   XRD or EDS. (The user noted it may feel inconsistent at first; the README's "Three folders"
   says it in these words.)
2. **Excel**: no answer yet; CSV/TSV now, `.xlsx` later as an optional extra (not in the image).
3. **Replay checks instead of redrawing** an imported figure: accepted.
4. **One agent per kind of analysis** (`<name>-analyst`), for consistency with the methods.
