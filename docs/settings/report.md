# Report

`analysis/report.html` is the page people read: the postdoc's answer, overview tables per method, and one card per measurement (plot, key numbers, checks, notes). It is rebuilt after every pass and by `labpipe report`.

## Settings

Defaults in `config/report.yaml`; yours in `config/lab.yaml`:

```yaml
report:
  group_by: sample             # sample | method | none
  sections: [postdoc, overview, measurements]   # order; leave one out to hide it
  cards: full                  # full | compact (first figure, key numbers, checks)
  methods: all                 # or [xrd, eds]
  samples: all                 # or IDs / patterns, e.g. ["S-1*"]
  extra:                       # more reports, written as analysis/report-<name>.html
    xrd-only: {methods: [xrd], group_by: none, cards: compact}
```

- `group_by: sample`: each sample with its measurements and image gallery (default).
- `group_by: method`: one part per method, cards ordered by sample.
- `group_by: none`: one list, ordered by sample, then method.
- Each extra report starts from your main report settings and changes only what it lists. The notifications page links all of them.

```bash
labpipe report      # rebuild now; prints the reports written
```

## You say → setting

| You say | Setting |
|---|---|
| "I want to see all XRD together" | `group_by: method` |
| "just one long list" | `group_by: none` |
| "hide the postdoc's part" | `sections: [overview, measurements]` |
| "too long, only plots and numbers" | `cards: compact` |
| "a separate page for the S-1x series" | `extra: {s1x: {samples: ["S-1*"]}}` |

## Notes checked with older rules

The checks on notes and on the postdoc's analyses get stricter with some releases. Text that passed before is not failed or rewritten because of that: the card (or the postdoc's section) stays as passed and shows a small line, "checked with the rules of labpipe <version>". Notes that really fail the rules they were checked with show the warning as before.

## After a change

Only the report is rebuilt: no re-analysis, no AI. An extra you remove from the settings keeps its old file (nothing is deleted); delete it yourself if you like.
