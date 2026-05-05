# pyquotex web API — single-tenant FastAPI server.
#
# Build:   docker build -t pyquotex-webapi .
# Run:     docker run --rm -p 8000:8000 \
#            -e PYQUOTEX_EMAIL=… -e PYQUOTEX_PASSWORD=… \
#            -e PYQUOTEX_API_KEY=… pyquotex-webapi
#
# Or use docker-compose.yml in the repo root.

FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY pyquotex ./pyquotex

RUN pip install --no-cache-dir --upgrade pip build \
 && pip wheel --no-cache-dir --wheel-dir /wheels '.[fast,webapi]'


FROM python:3.12-slim AS runtime

# Run as non-root.
RUN useradd --create-home --shell /bin/bash pyquotex
USER pyquotex
WORKDIR /home/pyquotex

# Install the wheels built in the previous stage.
COPY --from=builder /wheels /tmp/wheels
RUN pip install --user --no-cache-dir /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels

ENV PATH="/home/pyquotex/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYQUOTEX_HOST=0.0.0.0 \
    PYQUOTEX_PORT=8000

EXPOSE 8000

# Container-level health check hits the public /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "pyquotex.webapi"]
