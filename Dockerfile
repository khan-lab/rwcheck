# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools.
RUN pip install --upgrade pip hatchling

# Copy package definition first (layer-cache friendly).
COPY pyproject.toml .
COPY README.md .
COPY rwcheck/ rwcheck/
COPY rw_api/ rw_api/
COPY scripts/ scripts/

# Build a wheel.
RUN pip wheel --wheel-dir /wheels .

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="rwcheck"
LABEL org.opencontainers.image.description="Retraction Watch DOI/PMID checker API"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Install runtime dependencies from the built wheel.
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels rwcheck && rm -rf /wheels

# Create the data directory explicitly so it exists with correct ownership
# before Docker mounts the named volume over it.
RUN mkdir -p /app/data && chmod 755 /app/data

VOLUME ["/app/data"]

# Default environment — override at runtime.
ENV RW_DB_PATH=/app/data/rw.sqlite \
    RW_CSV_URL=https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv \
    RATE_LIMIT=60/minute \
    UPDATE_INTERVAL_HOURS=24 \
    PUBLIC_HOST=http://localhost:8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Default: run the CLI.  Override CMD to start the API server:
#   docker run rwcheck:latest uvicorn rw_api.main:app --host 0.0.0.0 --port 8000
# docker-compose.yml overrides this with the uvicorn command automatically.
#CMD ["uvicorn", "rw_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["rwcheck", "--help"]
