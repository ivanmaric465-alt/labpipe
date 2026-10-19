# sem: SEM images (JEOL, TIFF with .txt metadata)

Status: APPROVED 2026-10-17 (gallery picks across both series). Implemented.

## Input
- Instrument and software: JEOL JSM-7000F; images saved as 8-bit greyscale TIFF, 1280×1024, the bottom 64 px are JEOL's info bar (signal, kV, magnification, WD, scale bar).
- Files: `<name>.tif` plus a companion `<name>.txt` with the same stem (JEOL `$CM_…` metadata). The `.tif` is the measurement; the `.txt` belongs to it.
- Example: `SEM/S-10 a001.tif` + `SEM/S-10 a001.txt` (sample ID from the name; `a001` = series a, image 1).
- Metadata read from the `.txt`: date, time, instrument, accelerating voltage (kV), magnification, signal (SEI/BEI), working distance (mm), scale bar (length in px + label, e.g. 117 px = 100 nm), image resolution, scan speed, stage position.
- From the user: nothing required.

## What the user wants out
- Per image (all by code): the metadata above; **pixel size** (nm/px) = scale-bar length / scale-bar pixels; field of view (µm) = pixel size × image width/height without the info bar.
- Plot: the image itself (with JEOL's info bar), titled sample · image · magnification.
- Report, **per sample**: a small gallery with the images closest to **10k, 50k and 100k×** (`config/sem.yaml` → `gallery.magnifications`), each with magnification, kV, WD. Per sample or per measurement the user can change the magnifications, or name images to always include or exclude (`gallery.include`, `gallery.exclude`). Ties (two images equally close) go to the earlier series/index, so the choice is deterministic.

## How (deterministic)
1. Recognise: a `.tif` counts as SEM only if a JEOL `.txt` with the same stem exists (`recognise:`); the `.txt` itself is claimed by SEM as a companion (not a measurement, not "unrecognised"). Other `.tif` files stay unrecognised and are shown to the user.
2. Parse the `.txt` (UTF-8, falling back to cp1252; CRLF) into key/value pairs; numbers with their units.
3. Read the TIFF (Pillow, already installed with matplotlib): check its size against `$CM_IMAGE_RES`.
4. Pixel size and field of view from the scale bar (code; the label is parsed as value + unit nm/µm/mm).
5. Plot: the image, downscaled to the ACS double-column width (7 in, 300 dpi); the full-resolution original stays in the data folder.
- Defaults (`config/sem.yaml`): `info_bar_px: 64`, `gallery.magnifications: [10000, 50000, 100000]`, `gallery.include: []`, `gallery.exclude: []`, `plot.width_in: 7`.
- Code reused: the `$CM_` parsing idea from eln_refactor `utils/scientific_reports/instrument.py` (a3931a4). Its bugs are not carried over: it never read the accelerating voltage, never derived a pixel size, and its report never showed the metadata (reported earlier; not fixed there).

## Checks
| code | severity | when |
|---|---|---|
| `metadata_missing` | review | companion `.txt` absent or unreadable (only reachable via a manual entry) |
| `scale_bar_unreadable` | review | scale-bar pixels or label cannot be parsed: no pixel size |
| `image_size_mismatch` | warning | TIFF size differs from `$CM_IMAGE_RES` |
| `sample_id_from_name_only` | info | JEOL files carry no sample ID, so the file name is the only source |

## Analyst agent
- **No AI per image** (many images per sample; little to say that the metadata does not). `methods.yaml` → `subagent: null`, `notes: false`; automatic runs analyse SEM images with code only and start no agent for them.
- On request, in a conversation: the agent may describe selected images, marked as a qualitative description by an AI, never as a measurement (no particle sizes "by eye").

## Changes outside the method (needed for this design)
- Methods without an analyst: `notes todo` skips them; `labpipe automation run` analyses them with code before deciding whether an AI run is needed; `methods check` accepts `subagent: null`; `methods new --no-analyst`.
- Report: a per-sample section; SEM appears there as the gallery instead of one card per image.

## Settings (`config/sem.yaml`)

| User says | Setting |
|---|---|
| "show 5k and 200k images in the report" | `gallery.magnifications: [5000, 200000]` (per sample in `config/samples.yaml`) |
| "always show / never show that image" | `gallery.include: [<measurement_id>]` / `gallery.exclude: [...]` |
| "our info bar is taller" | `info_bar_px: …` |

## Interpretation
- No notes per image. Asked to describe an image, say it is a qualitative description by an AI, never a measurement; no particle sizes "by eye".

## Decisions and findings
- 2026-10-17, user: the gallery picks across both series.
- Sample IDs: names like `S-10 a001` need the space as a separator; the sample-ID rule now splits at the first `_` or space (`config/pipeline.yaml` → `sample_id.separators`). XRD sample IDs are unchanged.
- A sample without low-magnification images shows its lowest one for several targets (shown once).
- Open: images with `BEI` signal (none so far) are not kept apart from `SEI`.
