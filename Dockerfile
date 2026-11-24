# syntax=docker/dockerfile:1
# showquery: osqueryd + MongoDB extension + API + built UI in one image.
# MongoDB runs as its own service; see docker-compose.yml.

# ---- UI ---------------------------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- osquery (portable tarball, no root daemon or system service) ------------------------
FROM debian:bookworm-slim AS osquery
ARG OSQUERY_VERSION=5.24.1
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN case "$TARGETARCH" in \
      amd64) arch=x86_64 ;; arm64) arch=aarch64 ;; *) echo "unsupported: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/osquery.tgz \
      "https://github.com/osquery/osquery/releases/download/${OSQUERY_VERSION}/osquery-${OSQUERY_VERSION}_1.linux_${arch}.tar.gz" \
 && mkdir /osquery && tar -xzf /tmp/osquery.tgz -C /osquery \
 && /osquery/opt/osquery/bin/osqueryd --version

# ---- app --------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    OSQUERYD=/opt/osquery/bin/osqueryd \
    SHOWQUERY_RUN_DIR=/run/showquery \
    SHOWQUERY_OSQUERY_SOCKET=/run/showquery/osquery.sock \
    SHOWQUERY_WEB_DIST=/app/web/dist
COPY --from=osquery /osquery/opt/osquery /opt/osquery
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install . && useradd --system --uid 10001 showquery \
 && mkdir -p /run/showquery /app/config && chown showquery /run/showquery
# Explicit modes: host file permissions (often 0600) must not leak into the image.
COPY --chmod=0644 config/osquery.flags config/osquery.flags
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/showquery-entrypoint
COPY --from=web /web/dist /app/web/dist
USER showquery
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
ENTRYPOINT ["showquery-entrypoint"]
