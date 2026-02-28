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

LABEL org.opencontainers.image.title="rwcheck"
LABEL org.opencontainers.image.description="Retraction Watch DOI/PMID checker CLI"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels rwcheck && rm -rf /wheels

RUN mkdir -p /app/data && chmod 755 /app/data

VOLUME ["/app/data"]

ENV RW_DB_PATH=/app/data/rw.sqlite \
    RW_CSV_URL=https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv

ENTRYPOINT ["rwcheck"]
CMD ["--help"]
