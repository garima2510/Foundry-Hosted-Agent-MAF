FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# uv binary for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY 5-hosted-agent.py ./

ENV PATH="/app/.venv/bin:$PATH"

# Hosted agent runtime serves on 8088 locally; the platform handles routing in prod
EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8088/readiness')" || exit 1

CMD ["python", "-u", "5-hosted-agent.py"]
