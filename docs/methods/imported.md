# Your own analyses (made outside labpipe)

An Origin fit exported as PNG and CSV, a table from instrument software, a short write-up: put them in your data folder, tell labpipe which folder and which files, and they appear in the report next to labpipe's own analyses, marked as yours. labpipe copies, hashes and checks them. It never redoes, redraws or "corrects" them.

## How to

Put the files in their own folder of the data folder (labpipe only reads it). Files with the same name before the extension are **one measurement**; the sample ID is the start of that name, as for every file (`S-9_raman.png`, `S-9_raman.csv`, `S-9_raman.md` are one measurement of sample S-9):

```
Raman/fits/S-9_raman.png      the figure, as exported
Raman/fits/S-9_raman.csv      its table (optional)
Raman/fits/S-9_raman.md       how it was done, a remark (optional)
```

Say it to your agent: *"include my Raman fits from Raman/fits"*. It proposes the command, shows it, and runs it with your OK; then it helps you fill the description from what you tell it. Or run it yourself, in the project folder:

```bash
labpipe methods import raman_fits --folder "Raman/fits" --files "*.png,*.csv,*.md" \
  --description "Raman D/G fits made in Origin"
labpipe scan
labpipe analyze --pending
```

- One import per kind of analysis (`raman_fits`); a name of lower-case letters, digits and `_`.
- Sample ID in the folder name instead (`Raman/fits/S-9/fit.png`): add `--sample-from folder`.
- No notes by an agent, only the files in the report: add `--no-analyst`.
- Files nobody claims are reported as "not recognised"; this is the way to take your own analyses in.

It creates, and never overwrites, in the project:

| File | What |
|---|---|
| `lab/methods.yaml` | the entry: `kind: imported`, the `patterns` (`Raman/fits/**/*.png`, one per file type), `sample_from` |
| `lab/methods/raman_fits/README.md` | **the description of the analysis**: what was measured, software and model, what the columns mean with their units, what to look at in the figure. Fill it in; the first paragraph is shown on the cards, and the analyst reads all of it before it writes a note |
| `lab/methods/raman_fits/config.yaml` | `max_rows` and, optionally, `quantities` (below) |
| `lab/agents/raman_fits-analyst.md` | the analyst of this kind (not with `--no-analyst`) |

No Python is written: one shipped module (`labpipe.methods.imported`) handles every imported kind. In dev mode (the labpipe repository as its own project) the command refuses: importing is a project thing. After it: `labpipe project sync` so your agents see the new role.

## What labpipe does, and does not

Does (code, no AI):
- **One measurement per file name in a folder.** The first of the files by kind is the measurement's main file: a table, else an image, else a text, else a PDF (`.csv .tsv`, then `.png .jpg .tif`, then `.md .txt`, then `.pdf`). The others are recorded as "same measurement" and travel with it: when one of them is new or changes, the measurement is analysed again.
- Copies into `analysis/<sample>/<name>/<measurement>/`: `results.json` (each file with its path, sha256 and kind; the table as text, header and the first `max_rows` rows, every value exactly as written in your file; text as written), `plot.png` (your first image as PNG; a small "no figure: table only" figure when there is none), `figure-2.png`, … (further images), PDFs as they are, and `provenance.json` with every source file's sha256 and `made_by: "outside labpipe"`.
- **Named quantities** (optional): a table column becomes a quantity, so it shows in `labpipe dataset`, for the postdoc and in the comparison figures (dot plots). The value is copied from your table; only the text is read as a number. In `lab/methods/raman_fits/config.yaml`:

  ```yaml
  quantities:
    raman.id_ig:
      column: "ID/IG"            # header of the column in your table
      unit: ""
      sigma_column: "ID/IG sd"   # optional: a column with the uncertainty
      row: 1                     # optional: which data row (default 1, the first)
  ```
- **Checks** (`review`: look before using; `warning`: say it in the notes; `info`):

  | code | severity | when |
  |---|---|---|
  | `table_no_units` | warning | no header cell has a unit in brackets, e.g. `Raman shift (cm-1)` |
  | `quantity_column_missing` | review | a configured column is in no table |
  | `quantity_not_numeric` | review | the cell in that column and row is not a number |
  | `quantity_sigma_unreadable` | warning | the `sigma_column` is missing or not a number; no σ is recorded |
  | `quantity_row_assumed` | info | the table has several rows and `row:` was not set (row 1 is used) |
  | `description_missing` | warning | `README.md` is missing, has no paragraph, or still has the template's `TODO` |
  | `figure_without_table` | warning | a figure but no table: its numbers cannot be quoted |
  | `excel_not_read` | review | an `.xlsx`/`.xls`: Excel is not read yet, export the sheet as CSV |
  | `table_unreadable`, `image_unreadable`, `file_unreadable` | review | the file cannot be read; the reason is in the message, the other files are still read |
  | `table_truncated`, `text_truncated` | info | more than `max_rows` rows (default 200) or 20000 characters; the rest stays in your file |

Does not: redo, fit, redraw, convert units, round, or judge your analysis. Excel is not read (CSV and TSV are; a `.txt` with columns counts as a table). It never changes your files.

**Replay** (`labpipe replay <provenance.json>`) does not redraw an imported figure; it **checks** that every source file is still there with the recorded sha256 and that the copies in the project still match their recorded hashes, and says "made outside labpipe: checked, not redrawn". The comparison figures made from its quantities are labpipe's and replay as usual.

## Settings

| You say | Setting |
|---|---|
| "show more rows of the table" | `max_rows` in `lab/methods/<name>/config.yaml` (or per measurement: `config/measurements/<name>/<measurement>.yaml`) |
| "use ID/IG as a number to compare samples" | `quantities` (above) |
| "take the second row" | `row: 2` in that quantity |
| "no notes for these" | `notes: false` (and `subagent: null`) in the entry in `lab/methods.yaml`, or `--no-analyst` |
| "the sample is the folder name" | `sample_from: folder` in the entry |

Changing the settings or the description page marks the analyses out of date (they are redone with `labpipe requeue --stale`). Changing the entry's patterns takes effect at the next `labpipe scan`.

## Quantities

Only the ones you name in `quantities`, as `<name>.<quantity>` (your choice), value as in the table, σ from `sigma_column` or none, unit as you write it. They are offered to `labpipe dataset` and the comparison figures like those of the other methods.

## The analyst agent

`lab/agents/<name>-analyst.md` reads the description page first, then `labpipe <name> summary --entry …`, looks at `plot.png` and writes a short `notes.md` ([docs/WRITING.md](../WRITING.md)): what your analysis shows, numbers quoted only from your table (`labpipe notes verify` accepts a number that is in the table, and rejects an invented one), plot impressions marked "(plot)", and what is missing for a reader (units, σ, how the fit was done). It never redoes your analysis.

## Reading the card

- **The badge "your analysis, made outside labpipe"**: labpipe did not make or check the method; it copied your files. Trust the method as far as you trust your own work.
- Under it, **the first paragraph** of your description page, then **your figure(s)** (one at a time when there are several), **the table** exactly as in your file (scrolls; the note under it says how many rows of how many), **the texts** (open to read), **the files** it rests on and where the copies are.
- **Check before using**: the review checks above (a missing column, a table that is not read). **Worth knowing**: the warnings and notes (no units, no description, a figure without a table).
- **In plain words** is the analyst's note, with numbers from your table only.
