# Container

labpipe runs its commands and the automatic processing inside a Podman or Docker container: your data folder is mounted read-only, outgoing connections go only to the hosts listed, and the agent tool's login is kept in a container volume.

## Settings

`config/local.yaml` (written by `labpipe container setup`):

```yaml
container:
  engine: podman          # podman | docker
  popups: true            # Linux desktop pop-ups
  # name, image, port, allowed_hosts: defaults in config/pipeline.yaml → container
```

```bash
labpipe container setup              # writes the block, keeps existing keys
labpipe container start [--replace]  # (re)starts; builds the image when needed
labpipe container login claude|codex
labpipe container status | logs | stop
labpipe shell                        # a session inside the container
```

## You say → setting

| You say | Setting |
|---|---|
| "port 8765 is taken" | `container.port: 8766` |
| "two projects on one computer" | a different `container.name` and `port` per project |
| "the agent needs to reach X" | `container.allowed_hosts` (a deliberate security decision: say why in a comment) |

## After a change

`labpipe container start --replace`.
