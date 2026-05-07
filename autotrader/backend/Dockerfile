# Multi-stage build for the autotrader backend.
#
# Build context is the *repository root* (one level above autotrader/) so
# that the local-path dependency on pyquotex resolves. Build with:
#
#   docker build -f autotrader/backend/Dockerfile -t autotrader-api .
#
# The included docker-compose.yml in autotrader/ does this automatically.

FROM python:3.13-slim AS builder

# Pinned uv binary — much faster than pip, and reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

# Build deps for tgcrypto (C extension) and any source-only wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /repo

# Bring in the pyquotex sibling project — its pyproject.toml is the
# source-of-truth for that package, and uv resolves it via the
# tool.uv.sources entry in autotrader/backend/pyproject.toml.
COPY pyproject.toml README.md /repo/
COPY pyquotex /repo/pyquotex

# Bring in the autotrader backend.
COPY autotrader/backend /repo/autotrader/backend

WORKDIR /repo/autotrader/backend

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Install runtime dependencies into /opt/venv. --no-dev skips test/lint
# tooling. --no-editable produces a regular install so the wheel can be
# copied across stages cleanly.
RUN uv sync --no-dev --no-editable


# ----------------------------------------------------------------------------
# Runtime image — slim, non-root, healthcheck.
# ----------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

RUN useradd --create-home --shell /bin/bash --uid 1000 autotrader \
 && mkdir -p /data && chown autotrader:autotrader /data

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOTRADER_API_HOST=0.0.0.0 \
    AUTOTRADER_API_PORT=8000

USER autotrader
WORKDIR /home/autotrader

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "autotrader.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--no-access-log"]
