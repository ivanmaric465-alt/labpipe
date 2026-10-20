# {{method}}: {{description}}

Status: DRAFT, not yet agreed with the user. No code beyond the scaffold until it is.

## Input
- Instrument and software:
- Files ({{extension}}, companions):
- Example files (paths relative to the data folder):
- Metadata in the files, and what must come from the user instead:

## What the user wants out
- Numbers (each with unit and uncertainty):
- Plot(s):
- What goes into the report:

## How (deterministic)
- Steps, in order:
- Defaults (become `config/{{method}}.yaml`) and why:
- Code reused from elsewhere (source and commit; bugs found there are reported, not silently fixed):

## Checks
| code | severity | when |
|---|---|---|

## Reading the plot
<!-- Fill in before the method is used: 3 to 5 bullets for someone seeing the figure for the first time: the axes and their units, what each line, marker, colour, panel and label is, what a good figure looks like and what means "look again", where the numbers come from (results.json), and which `plot:` settings change it. Be true to the plot code. `labpipe methods check` warns while this section is missing or still this comment. -->
-

## Settings (what the user can change)
| User says | Setting |
|---|---|

## Quantities
- Named numbers worth comparing across samples, for `labpipe dataset` (`quantities(results)` in the summary module; each with value, σ, unit, key path in `results.json`), or "none":

## Report
- Optional layout hooks in the summary module (`table`, `overview_tables`, `CAPTION`; `docs/ADDING_A_METHOD.md`), or "none" (the report shows the `summarize` lines):

## Interpretation
- What may be named in notes, and how it is worded:

## Analyst agent
- What it looks at in the plot:
- What the notes say; what it must never claim:
- AI per measurement, or only on request (cost):

## Open questions for the user
-
