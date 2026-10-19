# Samples: presets, per-sample settings, properties

`config/samples.yaml` (git-ignored, your lab's) says what labpipe should know about each sample: settings shared by groups of samples (presets), settings for one sample, and numbers about the sample (nominal composition, loading) that the postdoc and `labpipe dataset` use.

## Settings

```yaml
presets:
  carbon_black:                     # shared by every sample that lists it
    note: "Vulcan XC-72 support"
    xrd: {broad_components_deg: [24.5, 43.0]}
samples:
  S-9:
    presets: [carbon_black]
    xrd: {phases: [Pt, Pd]}         # this sample only; overrides the presets
    eds: {nominal_at_percent: {Pt: 50, Pd: 50}}
    properties: {loading_wt: 20}    # numbers about the sample → `sample.loading_wt`
```

Order (later wins): `config/<method>.yaml` ← `config/lab.yaml` → `<method>` ← presets ← sample ← `config/measurements/<method>/<id>.yaml`. Keys must exist in `config/<method>.yaml`; the method pages list them (`docs/methods/<method>.md` → Settings).

## You say → setting

| You say | Setting |
|---|---|
| "S-10 to S-17 are all on carbon black" | `presets.carbon_black`, and `presets: [carbon_black]` on each sample |
| "S-9 should be 50/50 Pt–Pd" | `samples.S-9.eds.nominal_at_percent: {Pt: 50, Pd: 50}` |
| "S-9 has 20 wt% metal loading" | `samples.S-9.properties.loading_wt: 20` |
| "only for this one scan" | not here: `config/measurements/<method>/<measurement_id>.yaml` |

## After a change

The affected measurements become out of date (stale): `labpipe requeue --stale`, then they are re-analysed (code) and their notes rewritten (AI). Properties only change `labpipe dataset` and what the postdoc sees.
