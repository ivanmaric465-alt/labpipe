# xrd: powder X-ray diffraction (XRDML, .xy)

Status: implemented (before design notes existed; this page records how it works and how to steer it). Code taken from earlier projects and what changed: `docs/PORTING.md`.

This is one lab's formulation of an XRD analysis (its choices: model, background, indexing, sizing). It works as it is, but it is mainly a worked example: steer it with settings first; if your analysis needs to work differently, build your own method from it (`docs/ADDING_A_METHOD.md`).

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
| "show the intensity on a square-root / log scale" | `plot.y_scale: sqrt` / `log` (plot only) |
| "no residual panel" / "no phase tick rows" | `plot.residual_panel: false` / `plot.phase_ticks: false` (plot only) |
| "zoom in on 35–50°" | `plot.zoom_deg: [35, 50]`: an extra figure `zoom.png` after `plot.png` (plot only) |

## Interpretation
- Phase names come only from `phases.fcc_indexed[*].closest_references`, `phases.reference_candidates` or `internal_standard`, always worded as candidates ("consistent with", "closest reference").
- A pure-element match on an alloy is not an identification; the `reference_match_isostructural` check says so.
- Don't name a crystal phase that isn't in the results. σ: `a_sigma_angstrom`, `two_theta_sigma_deg`.

## Quantities
For `labpipe dataset` (`summary.quantities`), copied from `results.json`:
- `xrd.a.fcc`: lattice parameter (Å, σ) of the sample's fcc phase, the same name in every sample (`phases.fcc_indexed[i].a_angstrom`). With several fcc phases, the main one (not tentative, most reflections) is `fcc`, the others `fcc-2`, `fcc-3`. The note names the closest reference as a candidate: "possibly Pd (closest reference, Δa −0.07 %)".
- `xrd.size.fcc`: Scherrer size (nm) of the phase's first reflection, σ from the peak-width fit only (`size_strain[i].scherrer[j].size_nm`). A lower bound until an instrument calibration exists; its note says so.
- The internal standard has no quantity: its lattice parameter is a database or certificate value, not a result.

## Reference phases

```
labpipe xrd refs add path/to/file.cif --id NAME --structure-type fcc|rocksalt|hcp|bcc|diamond|rutile|other [--cod-id N] --note "why"
labpipe xrd refs list | build
```

- Two sets: the references that come with labpipe (code folder, `root: code` in `provenance.json`) and the lab's own, which `refs add` puts in the project's `lab/references/xrd/` (`root: project`; updates never touch them). A lab reference may not reuse a shipped id. `refs list` shows each one's `source`.
- Adding or rebuilding a reference changes the XRD analysis version: existing XRD analyses become out of date (`labpipe requeue --stale` redoes them; old results kept).
- `labpipe xrd refs` runs on your computer, never in the container (also in container mode). Building needs the optional pymatgen, which is not part of the base install. If it is missing, `refs build` (and `refs add`) print the exact command for this installation: `"<labpipe's python>" -m pip install "pymatgen==<pinned version>"`. Show it to the user, say it is only used to compute reflection lists and runs on their computer, and let them run it; never install it yourself. Whether you add references, and which, is your choice.
- Check the CIF first: cell edges in Å, not kX; ambient conditions; not a theoretical or hydrogen-loaded structure. `references/xrd/phases.yaml` → `excluded` lists examples of what to reject.
- COD is open data. ICSD/ICDD CIFs are licensed and must not be committed.
- `fcc` and `rocksalt` references also take part in the lattice-parameter comparison.

## Reading the plot
- **Top panel**: x is the diffraction angle 2θ in degrees, y the intensity in counts as recorded (`Intensity (10ⁿ counts)` when the numbers are large). Black thin line = the measured data; red line = the fitted model; dashed grey = the fitted background; dotted blue (only when broad bands such as a support were fitted) = background plus those bands. Faint red vertical lines mark the fitted peak positions. The numbers (peak positions, widths, lattice parameter, size) are in `results.json`; the figure only draws them.
- **Tick rows** (middle): one row per candidate phase, each tick a reflection expected at the fitted lattice parameter, labelled at the right (`fcc-1 a=3.8898` is the lattice parameter in Å, `(std)` the internal standard, `(cand.)` a reference candidate, `(tent.)` a tentative phase). Ticks that sit on a peak are what "consistent with" means; a row is a candidate, never an identification.
- **Residual** (bottom, `Res.`): data minus fit, with a grey zero line. Good: flat noise around zero, no larger at the peaks. Look again: a wave, a step or a spike at a peak (missed peak, wrong background, a holder or support peak); then check the review checks and steer with `background.*`, `peaks.*` or `exclude_ranges_deg` above.
- **Look again** also when the red fit leaves the data away from peaks (background too high or low: `background.anchor_ranges_deg`), or a clear peak has no red line or tick near it.
- **Plot settings** (`plot:`, see `docs/settings/plots.md`; they redraw only): `y_scale: sqrt | log` makes small peaks visible (the axis label says so), `residual_panel: false` and `phase_ticks: false` remove those panels, `zoom_deg: [lo, hi]` adds `zoom.png` (same panels, cut to that window; the title says "zoom"), `width_in`, `height_in`.

## Comparison figures
`labpipe compare --method xrd` (also part of `labpipe report`) draws two figures across samples into `analysis/_compare/xrd/<name>/` (`plot.png`, `plot.pdf`, `figure.html`, `provenance.json`); code only, from `results.json` and the raw files (read-only). Drawing: `compare.py`. Crystallite size stays a generic dot plot (`xrd.size.fcc`); name `xrd.a.fcc` in `report.compare` to get the generic dot plot of a as well.

### Reading the plot
- **`patterns`**: the measured patterns of all samples (S-1, S-2, …) stacked on one 2θ axis, each with a label at the right; the colour only tells traces apart. The offset is for display, so read positions and shapes, not heights across traces. The raw file is read again and must have the sha256 the analysis recorded; a measurement whose file is missing or changed is left out and the caption says which. Each trace is thinned to at most 2000 points (lowest and highest point of every bucket kept, so no peak is lost). Intensity uses the `plot.y_scale` setting. Ticks under the panel: one row per reference phase that at least one analysis found to be a candidate, at its predicted line positions (as recorded in `phases.reference_matches`). Peaks of S-1 and S-2 at the same tick: "consistent with" that reference; a shift between traces is a different lattice parameter, read it from the next figure. Ticks are candidates, never an identification.
- **`lattice-fcc`** (`lattice-fcc-2`, … for further phases): one row per sample, a dot per measurement at the lattice parameter a (Å) with its σ bar (`xrd.a.fcc`, copied from `results.json`). Dashed vertical lines: a of the candidate references, labelled. Dots whose σ bars overlap are not distinguishable; a dot near a line is "close to" that reference, not that phase.

## Tables in the report
- The overview has one row per measurement and fcc phase: lattice parameter a (Å) and its ± σ in separate cells so the numbers line up ("no σ" when the fit gave none), the closest reference and Δa (%), the Scherrer size of the first reflection ± σ (nm; a lower bound until an instrument calibration exists), the number of reflections (tent. = tentative) and Rwp (%). The card shows the same as quantity, value, ± σ. Layout only: `table` and `overview_tables` in `summary.py`.

## Conventions
- **Units**: 2θ in degrees; d and lattice parameters in Å; intensity in counts (as recorded); peak areas in counts·degree; crystallite size in nm; strain dimensionless (also given in %).
- **Plot**: full measured range; axis labels `2θ (°)` and `Intensity (counts)`, or `Intensity (10ⁿ counts)` when scaled; fit in red, background dashed grey, background + broad components dotted blue, one tick row per phase, residual panel.
- **Model**: Kα1/Kα2 doublets using the ratio from the file; pseudo-Voigt crystalline peaks (FWHM ≤ 4°), Gaussian broad components (5–10°); Chebyshev background; Scherrer K = 0.9 on the Kα1 FWHM; sizes are lower bounds until an instrument calibration exists (`sizing.calibration_file`).
