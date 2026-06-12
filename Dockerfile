FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && rm -rf /root/.cache/uv

FROM node:24-bookworm-slim AS codex

ARG CODEX_VERSION=0.139.0

RUN npm install -g \
    @openai/codex@${CODEX_VERSION} \
    @openai/codex-linux-x64@npm:@openai/codex@${CODEX_VERSION}-linux-x64

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    YKM_INDEX_PATH=/data/index \
    YKM_LOG_PATH=/data/logs/query-log.jsonl \
    YKM_INTAKE_PATH=/data/intake

COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /uvx /usr/local/bin/
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/bin/npm /usr/local/bin/npm
COPY --from=codex /usr/local/bin/npx /usr/local/bin/npx
COPY --from=codex /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -sf ../lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl git && \
    MISE_VERSION=v2026.6.2 MISE_INSTALL_EXT=tar.gz MISE_INSTALL_PATH=/usr/local/bin/mise \
      sh -c "$(curl -fsSL https://mise.run)" && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --home-dir /home/ykm --shell /usr/sbin/nologin ykm

WORKDIR /app

COPY --from=builder --chown=ykm:ykm /app/.venv ./.venv

RUN mkdir -p /data/index /data/logs /data/intake && \
    chown -R ykm:ykm /data/logs /data/intake /home/ykm && \
    chmod 755 /data /data/index && \
    chmod 700 /data/logs /data/intake

USER ykm

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/readyz', timeout=3).read()"

CMD ["sh", "-c", "ykm serve --index \"$YKM_INDEX_PATH\" --mode \"${YKM_AUTH_MODE:-local}\" --host 0.0.0.0 --port \"${YKM_PORT:-8765}\""]
