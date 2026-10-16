# eds: EDS quantification (Oxford INCA HTML reports)

Status: DRAFT for the user's approval (2026-10-16). No analysis code until approved.

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
- Report: one card per EDS report: the plot, the metal composition table, what to check.

## How (deterministic)
1. Recognise: `.htm`/`.html` with INCA markers ("Processing option", a Weight%/Atomic% table) (`recognise:`); its linked JPGs and `.ipj` files are claimed by EDS as companions, so `s-100.jpg` is never read as a sample "S-100".
2. Parse with the standard library HTML parser; numbers with `.` or `,` as decimal mark (never silently dropped); keep the Totals row.
3. Compute in code: metal basis, renormalised at%, comparison with nominal.
4. Duplicates: a report byte-identical to another (`S-11 - Copy.htm` = `S-11.htm`) is recorded as `duplicate`, not analysed. This is a general scanner rule (same content within a method = duplicate), useful for every method.
- Defaults (`config/eds.yaml`): `non_metals: [C, N, O, F, S, Si, Cl]`, `low_concentration_wt_percent: 1.0`, `nominal_tolerance_at_percent: 5.0` (points of metal-basis at%).
- Code reused: the INCA table idea from eln_refactor `utils/instrument_reports.py` (a3931a4). Its bugs are not carried over: "Wt% Sigma" columns counted as composition, decimal commas dropped silently, image links failing on case (reported earlier; not fixed there).

## Checks
| code | severity | when |
|---|---|---|
| `normalised_totals` | info | processing normalised to 100%: the analytical total is unknown |
| `no_uncertainty_reported` | warning | the export has no σ; small differences cannot be judged |
| `low_concentration` | warning | an element below `low_concentration_wt_percent` (near the detection limit) |
| `support_dominates` | info | non-metals are most of the signal (e.g. C 82 wt%): metal ratios rest on small peaks |
| `nominal_deviation` | review | a metal differs from nominal by more than `nominal_tolerance_at_percent` |
| `no_nominal` | info | no nominal composition given for this sample |
| `images_missing` | warning | a linked JPG is not found |

## Analyst agent
- Yes, per report (few reports; short notes are cheap and checked by `labpipe notes verify`).
- Looks at: the electron image (is the analysed area representative: agglomerates, empty support?), the spectrum (are the labelled metal peaks visible above background?).
- Notes say: the metal composition, how it compares with nominal, how much to trust it (normalised, no σ, low concentrations). Never claims a phase, a stoichiometry or an element the table does not contain.

## Open questions for the user
- Which elements count as "not metal" for the metal basis: C, N, O, F, S, Si, Cl as proposed?
- Tolerance for `nominal_deviation`: 5 at% points of the metal basis?
- Several reports per sample (e.g. a second site, or a lower magnification): show each, or also an average per sample? (An average would hide site-to-site variation; I would show each.)
