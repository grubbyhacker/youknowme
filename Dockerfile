FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    YKM_INDEX_PATH=/data/index \
    YKM_LOG_PATH=/data/logs/query-log.jsonl

RUN useradd --create-home --home-dir /home/ykm --shell /usr/sbin/nologin ykm

COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev

RUN mkdir -p /data/index /data/logs && \
    chown -R ykm:ykm /data/logs /home/ykm && \
    chmod 755 /data /data/index && \
    chmod 700 /data/logs

USER ykm

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/livez', timeout=3).read()"

CMD ["sh", "-c", "ykm serve --index \"$YKM_INDEX_PATH\" --mode \"${YKM_AUTH_MODE:-local}\" --host 0.0.0.0 --port \"${YKM_PORT:-8765}\""]
