# What was taken from earlier code, and what changed

Parts of labpipe were ported from the author's earlier, unpublished code (read-only, not imported at run time):

- `upstream-eln` @ `a3931a4`: an electronic lab notebook with XRD charts;
- `upstream-echem` @ `41b9c69`: an electrochemistry suite with journal plot presets.

| labpipe | Upstream | Change |
|---|---|---|
| `methods/xrd/xmlsafe.py` | `utils/scientific_reports/common.py::_parse_xml_rejecting_dtds` | Copied as-is. |
| `methods/xrd/io_xrdml.py` | `utils/scientific_reports/charts.py::_charts_from_xrdml` | See "XRDML reader". |
| `methods/xrd/io_xy.py` | `charts.py::_charts_from_xy` | Skipped lines are counted and reported. An optional 3rd column (intensity error) is kept. No thinning of points. Size limit is a parameter. |
| `methods/xrd/peaks.py::upstream_*` | `utils/analysis_methods/xrd.py::_ordered_unique`, `_peaks` | Copied as-is. `_ordered_unique` is used for sorting and de-duplication. `_peaks` is run only so its output is recorded in `results.json` → `upstream_peak_candidates`, for traceability. |
| `methods/xrd/peaks.py::find_candidates` | (replaces `_peaks` for analysis) | See "Peak candidates". |
| `plotting/acs.py` | `upstream-echem` `core/plotting.py::JOURNAL_PRESETS['ACS']` | Same sizes, fonts and line widths. Arial falls back to Liberation Sans, and the font actually used is recorded in provenance. |

Not taken: `_bounded_points` (it thins scans above 5000 points), the SVG chart code, and the ELN evidence or report structures.

## XRDML reader

Upstream problems, with what labpipe does instead:

1. **Only the first scan was read.** Upstream searched the whole document separately for the first `2Theta` positions and the first counts, so a multi-scan file could pair them up wrongly. labpipe reads every `<scan>` and pairs positions and counts within the same `<dataPoints>` block. If a file holds more than one scan, it is flagged for review.
2. **Explicit position lists were ignored.** labpipe uses `<listPositions>` when present.
3. **Counting time, attenuation factors and the intensity unit were ignored.** labpipe records them, and applies `beamAttenuationFactors` with a warning.
4. **Wavelengths and the sample `<id>` were not read.** labpipe reads Kα1, Kα2, Kβ, the Kα2/Kα1 ratio, the anode, slits, detector, timestamps and the sample `<id>`. A sample `<id>` that disagrees with the filename is flagged for review.
5. **Long scans were thinned.** `_bounded_points` kept every n-th point above 5000. That would have dropped two of every three points in a real 12,948-point scan. labpipe never thins data for analysis.
6. **The size limit was fixed at 5 MB.** It is now configurable, 50 MB by default.

On real measurement files where upstream did not thin, both readers give the same points: `tests/test_pipeline.py::test_ported_parsers_match_upstream`.

## Peak candidates

Upstream `_peaks` problems, measured on a real scan:

- **One reflection split into several candidates.** With no smoothing, the main reflection gave four candidates: 40.214, 40.240, 40.280 and 40.306°.
- **Top 10 by intensity only.** The strongest cluster used up most of the slots, so weak reflections could be lost.
- **Window width depends on step size.** The fixed 5-point window means its width in degrees changes with the scan step.
- **Positions limited to the scan step.** Positions are raw data points, precise only to the 0.013° step.
- **No background, fitting, Kα2 handling, phase work or size analysis.** The code says so itself in its limitations text.

The replacement:

1. Smooths the background-subtracted counts with a Savitzky–Golay filter.
2. Sets thresholds in units of Poisson noise.
3. Enforces a minimum separation in degrees.
4. Has no top-N cap.
5. Suppresses Kα2 shoulders.
6. Uses the candidates only as starting values for the profile fit.

## New code (not in upstream)

- `fitting.py`: whole-pattern fit of a polynomial background, broad Gaussian components (e.g. carbon-support bands) and pseudo-Voigt Kα1/Kα2 doublets. Uses analytic derivatives, prunes insignificant automatic peaks, and supports user anchor ranges for the background.
- `standard.py`: detects an internal standard (Si) and fits a 2θ zero correction (constant, or zero plus sample displacement).
- `lattice.py`: fcc indexing, weighted lattice parameter, Nelson–Riley diagnostic.
- `references.py` plus `refbuild.py` (`labpipe xrd refs add|build|list`): reference reflection lists from CIFs (pymatgen, build time only), and pattern matching that produces candidates only.
- `sizing.py`: Scherrer (K = 0.9, FWHM of Kα1), Williamson–Hall, optional Caglioti instrument correction.
