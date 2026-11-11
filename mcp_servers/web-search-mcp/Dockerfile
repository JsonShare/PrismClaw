# syntax=docker/dockerfile:1.7

FROM python:3.12.7-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.5.9 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --python /usr/local/bin/python .

FROM python:3.12.7-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    WSM_LOG_LEVEL=INFO
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10002 agentuser \
    && useradd -u 10002 -g agentuser -m -s /usr/sbin/nologin agentuser
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
USER agentuser
EXPOSE 9002
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9002/health', timeout=3).status==200 else 1)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "web_search_mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "9002"]
