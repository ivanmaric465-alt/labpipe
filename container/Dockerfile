# labpipe image. Build (repo root as context):
#   podman build -f container/Dockerfile -t localhost/labpipe:dev .
# The code is NOT in the image; the repository is mounted at /labpipe at runtime.
FROM docker.io/library/python@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
# (python:3.14-slim = Debian 13 trixie, Python 3.14.8, multi-arch index digest)

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git fonts-liberation iptables tinyproxy-bin util-linux libnotify-bin ripgrep procps nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && (getent passwd tinyproxy >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin tinyproxy) \
 && git config --system --add safe.directory /labpipe

COPY container/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock && rm /tmp/requirements.lock

# Claude Code: official native installer, pinned version; binary copied (not a symlink into /root).
COPY container/versions.env /tmp/versions.env
RUN . /tmp/versions.env \
 && apt-get update && apt-get install -y --no-install-recommends curl \
 && curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_CODE_VERSION" \
 && install -m 0755 "$(readlink -f /root/.local/bin/claude)" /usr/local/bin/claude \
 && rm -rf /root/.local /root/.claude /root/.cache /tmp/versions.env \
 && apt-get purge -y --auto-remove curl && rm -rf /var/lib/apt/lists/*

# Codex CLI: exact version from the lock file.
COPY container/package.json container/package-lock.json /opt/codex/
RUN cd /opt/codex && npm ci --no-audit --no-fund && npm cache clean --force \
 && ln -s /opt/codex/node_modules/.bin/codex /usr/local/bin/codex \
 && chmod -R a+rX /opt/codex

COPY container/labpipe /usr/local/bin/labpipe
COPY container/entrypoint.sh /usr/local/bin/labpipe-entrypoint
COPY container/tinyproxy.conf /etc/labpipe/tinyproxy.conf
RUN chmod 0755 /usr/local/bin/labpipe /usr/local/bin/labpipe-entrypoint

ENV HOME=/home/labpipe \
    LABPIPE_ROOT=/labpipe \
    LABPIPE_IN_CONTAINER=1 \
    PYTHONPATH=/labpipe/src \
    PYTHONDONTWRITEBYTECODE=1 \
    DISABLE_AUTOUPDATER=1 \
    HTTPS_PROXY=http://127.0.0.1:3128 HTTP_PROXY=http://127.0.0.1:3128 \
    https_proxy=http://127.0.0.1:3128 http_proxy=http://127.0.0.1:3128 \
    NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1

WORKDIR /labpipe
ENTRYPOINT ["/usr/local/bin/labpipe-entrypoint"]
CMD ["labpipe", "automation", "watch"]
