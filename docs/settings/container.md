# Container

labpipe runs its commands and the automatic processing inside a Podman or Docker container: your data folder is mounted read-only, outgoing connections go only to the hosts listed, and the agent tool's login is kept in a container volume.

Each project has its own container. The project folder is mounted read-write at `/project`; the labpipe code is in the image (`/opt/labpipe`). The container is named `labpipe-<project name>` with its own home volume, and its port is the first free one from 8765, saved in the project's `config/local.yaml`. `labpipe` typed anywhere inside the project runs in that project's container.

## Settings

`config/local.yaml` (written by `labpipe container setup`):

```yaml
container:
  engine: podman          # podman | docker
  popups: true            # Linux desktop pop-ups
  port: 8765              # saved by `container start` (the first free port from 8765)
  # name, image, allowed_hosts: defaults in config/pipeline.yaml → container
```

```bash
labpipe container setup              # writes the block, keeps existing keys
labpipe container start [--replace] [--dev]  # (re)starts; builds the image when needed
                                     # --dev mounts the code folder: code edits need no rebuild
labpipe container login claude|codex
labpipe container status | logs | stop
labpipe shell                        # a session inside the container
```

## You say → setting

| You say | Setting |
|---|---|
| "port 8765 is taken" | `container.port: 8766` |
| "two projects on one computer" | nothing: each project gets its own name, volume and port. To choose them: `container.name` and `container.port` |
| "the agent needs to reach X" | `container.allowed_hosts` (a deliberate security decision: say why in a comment) |

## After a change

`labpipe container start --replace`.
