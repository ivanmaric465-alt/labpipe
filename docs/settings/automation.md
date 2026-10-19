# Automatic processing and notifications

A watcher (inside the container) looks for new files, waits until they stop changing, analyses them (code), lets the analysts write notes (AI) in batches, rebuilds the report and tells you what needs you: on the notifications page and, on Linux, as desktop pop-ups. A nightly run is the safety net. The AI starts only when there is work.

## Settings

`config/local.yaml`:

```yaml
automation:
  check_every_seconds: 60
  settle_minutes: 3              # a file must stay unchanged this long
  nightly_at: "02:00"
  max_measurements_per_run: 10   # the rest follow in the next run
  max_budget_usd: null           # none: your subscription's own limits apply;
                                 # a USD limit per AI run only when paying per use (API key)
container:
  popups: true                   # Linux desktop pop-ups
```

```bash
labpipe container start              # starts the watcher and the notifications page
labpipe container status             # running? page link
labpipe container popups on|off      # Linux pop-ups
labpipe automation status            # last runs
```

The notifications page: `http://127.0.0.1:8765/?token=…` (`labpipe container status` prints it). It lists what needs you (unrecognised files, decisions, failed and out-of-date analyses) and the activity, and links the report.

## You say → setting

| You say | Setting |
|---|---|
| "my instrument writes slowly, it picks up half files" | a larger `settle_minutes` |
| "run fewer AI notes at a time" | a smaller `max_measurements_per_run` |
| "no pop-ups" | `labpipe container popups off` |
| "nightly run at 23:00" | `nightly_at: "23:00"`, then `labpipe container start --replace` |

## After a change

`labpipe container start --replace` restarts the watcher with the new settings.
