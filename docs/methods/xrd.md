# xrd: powder X-ray diffraction (XRDML, .xy)

Status: implemented (before design notes existed; this page records how it works and how to steer it). Code taken from earlier projects and what changed: `docs/PORTING.md`.

## Input
- PANalytical `.xrdml` (wavelengths and Kα2/Kα1 ratio read from the file); `.xy` two- or three-column exports (wavelength from `config/xrd.yaml` → `input.default_wavelength`). When both exist for one scan, the `.xy` is recorded as a duplicate of the `.xrdml`.
- From the user: expected phases (reference CIFs), internal standard and its certificate value, sample-holder or support peaks.

## Setup (ask the user)
- Which phases they expect, and whether references are missing (see "Reference phases").
- Internal standard (e.g. Si) and its certificate lattice parameter.
- Sample holders that give peaks, supports or substrates (e.g. carbon black bands): presets in `config/samples.yaml`.

## Settings (`config/xrd.yaml`)

| User says | Setting |
|---|---|
| "background too low/high around X" | `background.anchor_ranges_deg: [[lo, hi], …]` (peak-free ranges); lower `background.order` if it bends at the scan ends |
| "there's a peak at X you missed" / "that's not a peak" | `peaks.add_deg: [X]` / `peaks.remove_deg: [X]` |
| "ignore X–Y (holder, artefact)" | `exclude_ranges_deg: [[X, Y]]` |
| "the support bands are at …" / "no broad bands" | `broad_components_deg: [..]` / `[]` (`null` = automatic) |
| "only compare with Pt, Pd" | `phases: [Pt, Pd]` |
| "no internal standard here" | `internal_standard.phase: null` |
| "our Si standard has a = …" | `internal_standard.lattice_a_angstrom: …` (from the certificate the user gives you) |

## Interpretation
- Phase names come only from `phases.fcc_indexed[*].closest_references`, `phases.reference_candidates` or `internal_standard`, always worded as candidates ("consistent with", "closest reference").
- A pure-element match on an alloy is not an identification; the `reference_match_isostructural` check says so.
- Don't name a crystal phase that isn't in the results. σ: `a_sigma_angstrom`, `two_theta_sigma_deg`.

## Quantities
For `labpipe dataset` (`summary.quantities`), copied from `results.json`:
- `xrd.a.<phase id>`: lattice parameter (Å, σ) of each fcc-indexed phase, e.g. `xrd.a.fcc-1` (`phases.fcc_indexed[i].a_angstrom`). The id is numbered within one measurement, so check the note (closest reference) before comparing across samples.
- `xrd.size.<phase id>`: Scherrer size (nm) of the phase's first reflection, σ from the peak-width fit only (`size_strain[i].scherrer[j].size_nm`). A lower bound until an instrument calibration exists; its note says so.
- The internal standard has no quantity: its lattice parameter is a database or certificate value, not a result.

## Reference phases

```
labpipe xrd refs add path/to/file.cif --id NAME --structure-type fcc|rocksalt|hcp|bcc|diamond|rutile|other [--cod-id N] --note "why"
labpipe xrd refs list | build
```

- Check the CIF first: cell edges in Å, not kX; ambient conditions; not a theoretical or hydrogen-loaded structure. `references/xrd/phases.yaml` → `excluded` lists examples of what to reject.
- COD is open data. ICSD/ICDD CIFs are licensed and must not be committed.
- `fcc` and `rocksalt` references also take part in the lattice-parameter comparison.

## Conventions
- **Units**: 2θ in degrees; d and lattice parameters in Å; intensity in counts (as recorded); peak areas in counts·degree; crystallite size in nm; strain dimensionless (also given in %).
- **Plot**: full measured range; axis labels `2θ (°)` and `Intensity (counts)`, or `Intensity (10ⁿ counts)` when scaled; fit in red, background dashed grey, background + broad components dotted blue, one tick row per phase, residual panel.
- **Model**: Kα1/Kα2 doublets using the ratio from the file; pseudo-Voigt crystalline peaks (FWHM ≤ 4°), Gaussian broad components (5–10°); Chebyshev background; Scherrer K = 0.9 on the Kα1 FWHM; sizes are lower bounds until an instrument calibration exists (`sizing.calibration_file`).
