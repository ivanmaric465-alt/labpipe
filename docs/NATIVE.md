# Full native install (advanced)

The analysis runs on your computer, without a container. Use it to work on labpipe itself, or
where Podman and Docker are not available. The normal install (README → Install) needs none of
this.

## Install

Needs Python 3.14.

```bash
git clone https://github.com/ivanmaric465-alt/labpipe ~/.local/share/labpipe/app
LABPIPE_FULL=1 ~/.local/share/labpipe/app/install.sh      # or: install.sh --full
```

Optional extras go into labpipe's own environment, and only when a command asks for one. For
example `labpipe xrd refs build` needs pymatgen and prints the exact command to run (it uses
the environment's own Python and the pinned version).

## Set up

`labpipe setup` is the guided setup:
- the data folder and its read-only view;
- what labpipe recognises;
- the sandbox;
- the models per agent;
- a pointer to container mode for automatic processing.

```bash
labpipe setup                      # all steps
labpipe setup --only raw-data      # one step: raw-data, files, sandbox, agents or automation
```

## The read-only view

Without a container nothing mounts the data folder read-only. labpipe reads it through a
**read-only view** of the same files, which you set up once. Examples:

| System | View |
|---|---|
| Linux | `/srv/labdata-ro` |
| macOS | `~/labdata-ro` |
| Windows | `//localhost/labdata-ro` |

```bash
labpipe readonly-view --source ~/labdata/pd     # prints the commands for your system
```

You run the printed commands; some need admin rights, and labpipe never runs those. Setup
waits, checks the result and saves it. The ways to get a read-only folder: `docs/SAFETY.md`.

## Automatic processing

Nothing runs by itself without a container:
- `labpipe automation run` does one pass by hand;
- or tell the agent "process the new data".

## Tests

```bash
~/.local/share/labpipe/venv/bin/python -m pip install pytest
~/.local/share/labpipe/venv/bin/python -m pytest
```

## Uninstall

`uninstall.sh` as in the README. To remove a read-only view, an administrator runs
`sudo umount <view>`, then deletes its line from `/etc/fstab`; the script only names it.
