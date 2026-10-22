# Automatic processing and notifications

Automatic processing lives only in the project's container (the native systemd watcher of earlier versions is retired). A watcher inside it looks for new files, waits until they stop changing, analyses them (code), lets the analysts write notes (AI) in batches, rebuilds the report and tells you what needs you: on the notifications page and, on Linux, as desktop pop-ups. A nightly run is the safety net. The AI starts only when there is work.

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
  notify:
    desktop: true                # Linux pop-ups (notify-send) when the container has `popups`
    command: null                # optional program: ["prog", "{message}"]
container:
  popups: true                   # Linux desktop pop-ups
```

The container reads `automation:` once, when it starts: after a change run `labpipe container start --replace` (`labpipe automation setup` offers it). On macOS and Windows there are no pop-ups: the notifications page is the channel (it can ask the browser to show notifications). `claude:` / `codex:` (a path to the command) only matter for `labpipe automation run` by hand on the host.

## Start at login

```bash
labpipe automation setup             # timings, then the steps below (asks before each)
labpipe doctor                       # `autostart`: does the container start by itself at login?
```

| Your system | What makes the container start at login |
|---|---|
| Podman on Linux | `systemctl --user enable podman-restart.service` (setup runs it after your yes). Without logging in, an administrator runs `loginctl enable-linger $USER`: shown, never run by labpipe |
| Docker | nothing: Docker restarts the container when it starts (Docker Desktop: "Start Docker Desktop when you sign in") |
| Podman on macOS, Windows | start the podman machine at login (Podman Desktop: "Start at login"); untested |

Without container mode nothing runs by itself: `labpipe automation run`, or tell the agent "process the new data".

Old native units (`labpipe-watch.service`, `labpipe-nightly.service`, `labpipe-nightly.timer`) from earlier versions: `labpipe automation setup` removes them after you confirm (not `labpipe-dbus-proxy.service`, which belongs to pop-ups), or:

```bash
systemctl --user disable --now labpipe-watch.service labpipe-nightly.service labpipe-nightly.timer
rm ~/.config/systemd/user/labpipe-watch.service ~/.config/systemd/user/labpipe-nightly.service ~/.config/systemd/user/labpipe-nightly.timer
systemctl --user daemon-reload
```

```bash
labpipe container start              # starts the watcher and the notifications page
labpipe container status             # running? page link
labpipe container popups on|off      # Linux pop-ups
labpipe automation status            # last runs
```

- **Measurements to check** (code-only methods: SEM, and your own methods without an analyst): one notification each time a measurement finishes flagged for review ("N SEM measurement(s) to check: image (check code), …"), with what to say to your agent ("check the review queue"); also in `logs/review_queue.md`. Told once per analysis.
- **Code-only analyses** run one measurement per fresh process (`labpipe <method> analyze --entry ID`), so edited lab method code is never run stale and the recorded version is that of the code that ran; a process that dies is recorded as failed with the end of its error output.
- **The watcher restarts itself** (the container restarts it) when labpipe's code changes or when anything in `lab/methods/` or `lab/methods.yaml` changes (logged as "lab methods changed").

The notifications page: `http://127.0.0.1:8765/?token=…` (`labpipe container status` prints it). It lists what needs you (unrecognised files, decisions, failed and out-of-date analyses) and the activity, and links the report.

## You say → setting

| You say | Setting |
|---|---|
| "my instrument writes slowly, it picks up half files" | a larger `settle_minutes` |
| "run fewer AI notes at a time" | a smaller `max_measurements_per_run` |
| "no pop-ups" | `labpipe container popups off` |
| "start the container when I log in" | `labpipe automation setup` |
| "nightly run at 23:00" | `nightly_at: "23:00"`, then `labpipe container start --replace` |

## After a change

`labpipe container start --replace` restarts the watcher with the new settings (the watcher reads them only at its start).
