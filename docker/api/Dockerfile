# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip hatchling

COPY pyproject.toml README.md ./
COPY rwcheck/ rwcheck/
COPY rw_api/ rw_api/
COPY scripts/ scripts/
COPY docs/ docs/

RUN pip wheel --wheel-dir /wheels .

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="rwcheck-api"
LABEL org.opencontainers.image.description="Retraction Watch DOI/PMID checker REST API"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels rwcheck && rm -rf /wheels

COPY --from=builder /build/docs/rwcheck_logo.png /app/docs/rwcheck_logo.png

RUN mkdir -p /app/data && chmod 755 /app/data

VOLUME ["/app/data"]

ENV RW_DB_PATH=/app/data/rw.sqlite \
    RW_CSV_URL=https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv \
    RATE_LIMIT=60/minute \
    UPDATE_INTERVAL_HOURS=24 \
    PUBLIC_HOST=http://localhost:8000

EXPOSE 8000

# start_period allows the full DB download + ingest before health checks count.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "rw_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
