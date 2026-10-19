# labpipe image: the dependencies, the agent tools and the labpipe CODE at /opt/labpipe.
# Projects are not in the image: a project folder is mounted at /project at runtime.
# Build with `labpipe container build`: it stages the build context (exactly the files the
# image hash covers: src, default config, methods.yaml, references, agents, templates, docs,
# AGENTS.md/CLAUDE.md, .claude/.codex/.agents sources, hooks, container/, pyproject.toml), so
# the lab's own files in a repository never get into the image.
FROM docker.io/library/python@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
# (python:3.14-slim = Debian 13 trixie, Python 3.14.8, multi-arch index digest)

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git fonts-liberation iptables tinyproxy-bin util-linux libnotify-bin ripgrep procps nodejs npm poppler-utils \
 && rm -rf /var/lib/apt/lists/* \
 && (getent passwd tinyproxy >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin tinyproxy) \
 && git config --system --add safe.directory /opt/labpipe \
 && git config --system --add safe.directory /project

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

# The code, last: a code change only rebuilds from here.
COPY . /opt/labpipe
# readable by the unprivileged user whatever the file modes on the host were
RUN chmod -R a+rX,go-w /opt/labpipe

ENV HOME=/home/labpipe \
    LABPIPE_CODE=/opt/labpipe \
    LABPIPE_PROJECT=/project \
    LABPIPE_IN_CONTAINER=1 \
    LABPIPE_PYTHON=/usr/local/bin/python \
    PYTHONPATH=/opt/labpipe/src \
    PYTHONDONTWRITEBYTECODE=1 \
    DISABLE_AUTOUPDATER=1 \
    HTTPS_PROXY=http://127.0.0.1:3128 HTTP_PROXY=http://127.0.0.1:3128 \
    https_proxy=http://127.0.0.1:3128 http_proxy=http://127.0.0.1:3128 \
    NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1

WORKDIR /project
ENTRYPOINT ["/usr/local/bin/labpipe-entrypoint"]
CMD ["labpipe", "automation", "watch"]
