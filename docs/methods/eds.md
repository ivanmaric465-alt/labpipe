# eds: EDS quantification (Oxford INCA HTML reports)

Status: APPROVED 2026-10-17 with changes (below). Implemented.

## Input
- Instrument and software: Oxford Instruments INCA (on the JEOL SEM), "Project" HTML export.
- Files: `<name>.htm` (the measurement) plus the two JPGs it links (`<IMG SRC="S-100.jpg">`, electron image with the analysed area, and the spectrum). The `.ipj` INCA project files are binary: recorded as belonging to EDS, not read.
- Example: `EDS/S-10.htm` with `EDS/s-100.jpg`, `EDS/s-101.jpg`. Links and file names differ in case; matching is case-insensitive within the same folder.
- In the `.htm`: spectrum processing, processing option (e.g. "All elements analyzed (Normalised)"), number of iterations, standards per element, one table Element / Weight% / Atomic% with X-ray line (C K, Ru L, Pt M…), Totals, comment.
- Not in the export: uncertainties (σ), accelerating voltage, live time. From the user (optional): the **nominal composition** per sample in `config/samples.yaml`:
  ```yaml
  samples:
    S-9: {eds: {nominal_at_percent: {Ru: 20, Rh: 20, Pd: 20, Ir: 20, Pt: 20}}}
  ```

## What the user wants out
- Per report (all by code): the table as recorded (wt%, at%, line), standards, processing; the **metal-only composition**: at% of the metals renormalised to 100 (C, O, N, S… excluded, list in `config/eds.yaml`); with a nominal composition: measured − nominal per metal (at% points) and measured/nominal.
- Plot (7 in wide): the electron image with the analysed area | the spectrum image | bar chart of metal at%, measured next to nominal.
- Report: one card per EDS report: the panels one at a time (electron image, spectrum, bar chart; `figures` in results.json), the metal composition table, what to check.

## How (deterministic)
1. Recognise: `.htm`/`.html` with INCA markers ("Processing option", a Weight%/Atomic% table) (`recognise:`); its linked JPGs and `.ipj` files are claimed by EDS as companions, so `s-100.jpg` is never read as a sample "S-100".
2. Parse with the standard library HTML parser; numbers with `.` or `,` as decimal mark (never silently dropped); keep the Totals row.
3. Compute in code: metal basis, renormalised at%, comparison with nominal.
4. Duplicates: a report byte-identical to another (`S-11 - Copy.htm` = `S-11.htm`) is recorded as `duplicate`, not analysed. This is a general scanner rule (same content within a method = duplicate), useful for every method.
- Defaults (`config/eds.yaml`): `non_metals: [C, N, O, F, S, Si, Cl]`, `low_concentration_wt_percent: 1.0`, `nominal_at_percent: null`, `images: null` (the linked ones).
- Code reused: the INCA table idea from eln_refactor `utils/instrument_reports.py` (a3931a4). Its bugs are not carried over: "Wt% Sigma" columns counted as composition, decimal commas dropped silently, image links failing on case (reported earlier; not fixed there).

## Checks
| code | severity | when |
|---|---|---|
| `normalised_totals` | info | processing normalised to 100%: the analytical total is unknown |
| `no_uncertainty_reported` | warning | the export has no σ; small differences cannot be judged |
| `low_concentration` | warning | an element below `low_concentration_wt_percent` (near the detection limit) |
| `support_dominates` | info | non-metals are most of the signal (e.g. C 82 wt%): metal ratios rest on small peaks |
| `no_nominal` | info | no nominal composition given for this sample |
| `images_missing` | warning | a linked JPG (or one named in `images`) is not found |
| `linked_images_other_name` | review | the report links images named for another report |
| `images_from_settings` | info | `images:` in the settings replaces the linked images |

## Analyst agent
- Yes, per report (few reports; short notes are cheap and checked by `labpipe notes verify`).
- Looks at: the electron image (is the analysed area representative: agglomerates, empty support?), the spectrum (are the labelled metal peaks visible above background?).
- Notes say: the metal composition, how it compares with nominal, how much to trust it (normalised, no σ, low concentrations). Never claims a phase, a stoichiometry or an element the table does not contain.

## Settings (`config/eds.yaml`)

| User says | Setting |
|---|---|
| "S-9 should be equimolar Ru, Rh, Pd, Ir, Pt" | `samples.S-9.eds.nominal_at_percent: {Ru: 20, Rh: 20, Pd: 20, Ir: 20, Pt: 20}` (copy the user's numbers; never compute them) |
| "count Si as a metal" / "leave out Cu (grid)" | `non_metals: [...]` |
| "the images of S-14 are s-140.jpg and s-141.jpg" (the report links the wrong ones) | `config/measurements/eds/S-14.yaml`: `images: [s-140.jpg, s-141.jpg]` plus a `note:` |

## Interpretation
- Elements only from the table; never a phase, a stoichiometry or an element the table does not contain.
- Compositions are of the analysed area only; normalised totals and the missing σ limit how far small differences can be trusted (say so).

## Decisions and findings
- 2026-10-17, user: all elements stay in the report (full table as recorded); the metal basis is shown in addition. No flag for the deviation from nominal (numbers only). Each report is shown separately, no averages.
- Metal basis from wt% / standard atomic weight (IUPAC), not from the recorded at%: INCA rounds at% to 0.01, so a minor metal at 0.03 at% carries ±17 % rounding error, while its wt% (e.g. 0.27) has more digits. The at%-based version is kept in results.json for comparison.
- Added check `linked_images_other_name` (review): in real data one report links the images named for another report while images named for itself exist next to it. labpipe does not guess which are right; the user sets them per measurement (`images: [s-140.jpg, s-141.jpg]` in `config/measurements/eds/<measurement_id>.yaml`, 2026-10-19). JPGs named for a report (`s-140.jpg` next to `S-14.htm`) are claimed by EDS even when no report links them.
- 2026-10-19, user: report figures shown one at a time with arrows (as in their ELN reports), so the panels are large; SEM galleries the same way.
- Duplicates: byte-identical reports (`X - Copy.htm`) are recorded as duplicates by the general scanner rule.
