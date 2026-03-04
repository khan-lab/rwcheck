"""rwcheck REST API — FastAPI application.

Configuration (environment variables)
--------------------------------------
RW_DB_PATH              Path to the SQLite database  (default: data/rw.sqlite)
RW_CSV_URL              URL of the Retraction Watch CSV
RATE_LIMIT              slowapi limit string          (default: 60/minute)
UPDATE_INTERVAL_HOURS   Hours between auto-updates    (default: 24)

Endpoints
---------
GET  /               — NiceGUI landing page
GET  /health         — liveness probe (unversioned)
GET  /api/v1/meta           — dataset provenance
GET  /api/v1/stats          — aggregate statistics (cached)
GET  /api/v1/check/doi/{doi}  — ``{doi}`` may contain slashes (path parameter)
GET  /api/v1/check/pmid/{pmid}
GET  /api/v1/check/title/{title}
GET  /api/v1/search         — filter by journal/author/country/publisher/reason/year
POST /api/v1/check/batch    — body: {dois: [], pmids: []}
POST /api/v1/check/bib      — BibTeX file upload
GET  /api/v1/enrich/doi/{doi} — CrossRef + OpenAlex metadata enrichment
GET  /api/v1/reports/{id}/html — serve shared HTML report (anyone with link)
GET  /api/v1/reports/{id}/zip  — download ZIP of all report formats
DEL  /api/v1/reports/{id}      — delete a report from the server

Environment variables
---------------------
RW_REPORTS_DIR          Directory for persistent .bib reports (default: rw_reports)
RW_REPORT_MAX_AGE_DAYS  Days before auto-deletion                (default: 7)
"""

from __future__ import annotations

import asyncio
import base64
import json as _json
import logging
import os
import re as _re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pycountry
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from rw_api.models import (
    BatchRequest,
    BatchResponse,
    BatchResult,
    BibCheckEntry,
    BibCheckResponse,
    CrossRefMeta,
    EnrichResponse,
    MatchResponse,
    MetaResponse,
    OpenAlexMeta,
    RecordSummary,
    SearchResponse,
    StatsResponse,
    TitleMatchResponse,
)
from rwcheck.bib import parse_bib_file
from rwcheck.db import (
    get_meta,
    get_stats,
    query_batch,
    query_by_doi,
    query_by_pmid,
    query_by_title,
    search_records,
)
from rwcheck.normalize import normalize_pmid

log = logging.getLogger("rw_api")

# ── Configuration ──────────────────────────────────────────────────────────────

_DB_PATH = Path(os.environ.get("RW_DB_PATH", "data/rw.sqlite"))
_CSV_URL = os.environ.get(
    "RW_CSV_URL",
    "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv",
)
_RATE_LIMIT = os.environ.get("RATE_LIMIT", "60/minute")
_UPDATE_INTERVAL_HOURS = int(os.environ.get("UPDATE_INTERVAL_HOURS", "24"))
_PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "http://localhost:8000").rstrip("/")
_REPORTS_DIR = Path(os.environ.get("RW_REPORTS_DIR", "rw_reports"))
_REPORT_MAX_AGE_DAYS = int(os.environ.get("RW_REPORT_MAX_AGE_DAYS", "7"))
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Maximum items allowed in a single batch request.
_BATCH_MAX = 500

# ── DB connection pool (thread-local) ──────────────────────────────────────────

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local read-only connection; re-open if the DB was replaced."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        from rwcheck.db import get_connection  # noqa: PLC0415

        conn = get_connection(_DB_PATH)
        _local.conn = conn
    return conn


def _close_thread_conn() -> None:
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn:
        conn.close()
        _local.conn = None


# ── Background DB update & report cleanup ─────────────────────────────────────

def _cleanup_old_reports() -> None:
    """Remove report directories older than _REPORT_MAX_AGE_DAYS days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_REPORT_MAX_AGE_DAYS)
    for report_dir in _REPORTS_DIR.iterdir():
        if not report_dir.is_dir():
            continue
        meta_file = report_dir / "meta.json"
        try:
            created = datetime.fromisoformat(
                _json.loads(meta_file.read_text())["created_at"]
            )
            # Ensure timezone-aware for comparison
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                shutil.rmtree(report_dir, ignore_errors=True)
                log.info("Deleted old report %s", report_dir.name)
        except Exception:  # noqa: BLE001
            pass  # malformed/missing meta — leave it alone


def _update_db_task() -> None:
    """Called by APScheduler; downloads the CSV and rebuilds the DB if changed."""
    from scripts.build_db import build_db  # noqa: PLC0415

    log.info("Running scheduled DB update…")
    try:
        build_db(csv_path=None, url=_CSV_URL, db_path=_DB_PATH, force=False)
        # Invalidate cached meta and stats after a rebuild.
        _cached_meta.cache_clear()
        _cached_stats.cache_clear()
        # Close thread-local connections so they re-open against the new file.
        _close_thread_conn()
        log.info("DB update complete.")
    except Exception:
        log.exception("DB update failed.")


# ── Cached meta (cleared on rebuild) ──────────────────────────────────────────

@lru_cache(maxsize=1)
def _cached_meta() -> dict[str, str]:
    return get_meta(_get_conn())


@lru_cache(maxsize=1)
def _cached_stats() -> dict[str, Any]:
    return get_stats(_get_conn())


def _meta_response() -> MetaResponse:
    m = _cached_meta()
    return MetaResponse(
        dataset_version=m.get("dataset_version", "unknown"),
        built_at=m.get("built_at", "unknown"),
        row_count=m.get("row_count", "0"),
        source_url=m.get("source_url", ""),
        csv_sha256=m.get("csv_sha256"),
    )


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _update_db_task,
        trigger="interval",
        hours=_UPDATE_INTERVAL_HOURS,
        id="db_update",
    )
    scheduler.add_job(
        _cleanup_old_reports,
        trigger="interval",
        hours=12,
        id="cleanup_reports",
    )
    scheduler.start()

    # Run an initial update in a background thread so the API is available
    # immediately (even if the DB already exists).
    t = threading.Thread(target=_update_db_task, daemon=True, name="initial-db-update")
    t.start()

    log.info(
        "rwcheck API started. DB=%s, update_interval=%dh", _DB_PATH, _UPDATE_INTERVAL_HOURS
    )
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)


# ── App factory ────────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=[_RATE_LIMIT])

app = FastAPI(
    title="RWCheck REST API",
    description=(
        "Query the Retraction Watch dataset by DOI, PubMed ID or BibTeX file. "
        "The local SQLite database is rebuilt automatically every day."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# All versioned API routes are registered on this router and mounted at /api/v1.
api_v1 = APIRouter(prefix="/api/v1")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _db_available() -> bool:
    return _DB_PATH.exists()


def _require_db() -> None:
    if not _db_available():
        raise HTTPException(
            status_code=503,
            detail=(
                f"Database not found at '{_DB_PATH}'. "
                "The background update is running; please retry in a moment."
            ),
        )


def _to_summary(row: dict[str, Any]) -> RecordSummary:
    return RecordSummary.model_validate({k: row.get(k) for k in RecordSummary.model_fields})


# ── Logo (embedded as base64 data URI so the page works without static files) ──

def _load_logo() -> str:
    # In a dev editable install __file__ is inside the project root, so
    # parent.parent resolves correctly.  In a wheel install (Docker) __file__
    # is inside site-packages, so fall back to the CWD which uvicorn inherits
    # from the Dockerfile WORKDIR (/app) where the logo is explicitly copied.
    candidates = [
        Path(__file__).parent.parent / "docs" / "rwcheck_logo.png",
        Path.cwd() / "docs" / "rwcheck_logo.png",
    ]
    for logo in candidates:
        if logo.exists():
            return f"data:image/png;base64,{base64.b64encode(logo.read_bytes()).decode()}"
    return ""


_LOGO_DATA_URI = _load_logo()






# ── Country → ISO-3 mapping ────────────────────────────────────────────────────

# Hardcoded overrides for names that pycountry doesn't resolve correctly.
_ISO3_OVERRIDES: dict[str, str | None] = {
    "United States": "USA",
    "South Korea": "KOR",
    "North Korea": "PRK",
    "Taiwan": "TWN",         # not UN-recognised but has ISO 3166-1 code
    "Russia": "RUS",         # pycountry uses "Russian Federation"
    "Iran": "IRN",           # "Iran, Islamic Republic of"
    "Czech Republic": "CZE", # now "Czechia" in pycountry
    "Palestine": "PSE",      # "Palestine, State of"
    "Vietnam": "VNM",        # "Viet Nam"
    "Bolivia": "BOL",        # "Bolivia, Plurinational State of"
    "Venezuela": "VEN",      # "Venezuela, Bolivarian Republic of"
    "Moldova": "MDA",        # "Moldova, Republic of"
    "Laos": "LAO",           # "Lao People's Democratic Republic"
    "Syria": "SYR",          # "Syrian Arab Republic"
    "Tanzania": "TZA",       # "Tanzania, United Republic of"
    "Kosovo": None,          # disputed; no standard ISO-3
    "Unknown": None,
}


def _map_country_to_iso3(name: str) -> str | None:
    """Return the ISO 3166-1 alpha-3 code for a country name, or None."""
    name = name.strip()
    if name in _ISO3_OVERRIDES:
        return _ISO3_OVERRIDES[name]
    try:
        return pycountry.countries.lookup(name).alpha_3
    except LookupError:
        try:
            results = pycountry.countries.search_fuzzy(name)
            return results[0].alpha_3 if results else None
        except LookupError:
            return None




@api_v1.get(
    "/stats",
    response_model=StatsResponse,
    tags=["meta"],
    summary="Dataset statistics",
)
def stats_endpoint() -> StatsResponse:
    """Return aggregate statistics: total records, distinct journals/countries/authors,
    DOI/PMID coverage, retractions per year, and top-10 journals by count.
    Results are cached and cleared on each daily database rebuild.
    """
    _require_db()
    s = _cached_stats()
    return StatsResponse(**s, meta=_meta_response())


@api_v1.get(
    "/meta",
    response_model=MetaResponse,
    tags=["meta"],
    summary="Dataset metadata",
)
@limiter.limit(_RATE_LIMIT)
async def get_meta_endpoint(request: Request) -> MetaResponse:
    """Return dataset provenance: version, build timestamp, row count, source URL."""
    _require_db()
    return _meta_response()


@api_v1.get(
    "/check/doi/{doi:path}",
    response_model=MatchResponse,
    tags=["lookup"],
    summary="Look up a DOI",
)
@limiter.limit(_RATE_LIMIT)
async def match_doi(doi: str, request: Request) -> MatchResponse:
    """Check whether *doi* appears in the Retraction Watch dataset.

    The DOI is normalised before lookup (URL prefixes and ``doi:`` stripped,
    lowercased).  Returns ``matched: false`` rather than a 404 when not found.
    """
    _require_db()
    conn = _get_conn()
    rows = query_by_doi(conn, doi)
    return MatchResponse(
        query=doi,
        matched=bool(rows),
        matches=[_to_summary(r) for r in rows],
        meta=_meta_response(),
    )


@api_v1.get(
    "/check/pmid/{pmid}",
    response_model=MatchResponse,
    tags=["lookup"],
    summary="Look up a PubMed ID",
)
@limiter.limit(_RATE_LIMIT)
async def match_pmid(pmid: str, request: Request) -> MatchResponse:
    """Check whether *pmid* appears in the Retraction Watch dataset."""
    _require_db()
    norm = normalize_pmid(pmid)
    if norm is None:
        raise HTTPException(status_code=422, detail=f"'{pmid}' is not a valid PMID.")
    conn = _get_conn()
    rows = query_by_pmid(conn, norm)
    return MatchResponse(
        query=pmid,
        matched=bool(rows),
        matches=[_to_summary(r) for r in rows],
        meta=_meta_response(),
    )


@api_v1.get(
    "/check/title/{title}",
    response_model=TitleMatchResponse,
    tags=["lookup"],
    summary="Look up by exact title (case-insensitive)",
)
@limiter.limit(_RATE_LIMIT)
async def match_title(title: str, request: Request) -> TitleMatchResponse:
    """Check whether *title* exactly matches (case-insensitively) a record in
    the Retraction Watch dataset.

    Results should be verified with a DOI or PMID — title data quality varies.
    Returns ``matched: false`` rather than a 404 when not found.
    """
    _require_db()
    conn = _get_conn()
    rows = query_by_title(conn, title)
    return TitleMatchResponse(
        query=title,
        match_type="title",
        matched=bool(rows),
        matches=[_to_summary(r) for r in rows],
        meta=_meta_response(),
    )


@api_v1.get(
    "/search",
    response_model=SearchResponse,
    tags=["lookup"],
    summary="Filter records by field values",
)
@limiter.limit(_RATE_LIMIT)
async def search_endpoint(
    request: Request,
    journal: str | None = Query(None, description="Exact journal name (case-insensitive)."),
    author: str | None = Query(None, description="Partial author name (case-insensitive)."),
    country: str | None = Query(None, description="Partial country name (case-insensitive); matches multi-country values like 'China;United States'."),
    publisher: str | None = Query(None, description="Partial publisher name (case-insensitive)."),
    reason: str | None = Query(None, description="Partial retraction reason (case-insensitive)."),
    year: int | None = Query(None, description="Retraction year (e.g. 2020)."),
    published_year: int | None = Query(None, description="Original paper publication year."),
    limit: int = Query(100, ge=1, le=500, description="Page size (max 500)."),
    offset: int = Query(0, ge=0, description="Page offset."),
) -> SearchResponse:
    """Filter Retraction Watch records by one or more field values.

    All provided filters are combined with AND logic.
    Text filters use case-insensitive partial (substring) matching.
    At least one filter parameter must be provided.
    """
    _require_db()
    if all(
        v is None for v in (journal, author, country, publisher, reason, year, published_year)
    ):
        raise HTTPException(status_code=400, detail="At least one filter parameter is required.")
    conn = _get_conn()
    total, records = search_records(
        conn,
        author=author,
        country=country,
        journal=journal,
        publisher=publisher,
        reason=reason,
        year=year,
        published_year=published_year,
        limit=limit,
        offset=offset,
    )
    return SearchResponse(
        total=total,
        limit=limit,
        offset=offset,
        results=[_to_summary(r) for r in records],
        meta=_meta_response(),
    )


@api_v1.post(
    "/check/batch",
    response_model=BatchResponse,
    tags=["lookup"],
    summary="Batch DOI and/or PMID lookup",
)
@limiter.limit(_RATE_LIMIT)
async def match_batch(body: BatchRequest, request: Request) -> BatchResponse:
    """Check multiple DOIs and/or PMIDs in one call.

    Maximum **500** items per request (combined dois + pmids count).
    """
    _require_db()
    if body.total() > _BATCH_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"Batch exceeds maximum size of {_BATCH_MAX} items.",
        )
    conn = _get_conn()
    raw_results = query_batch(conn, dois=body.dois, pmids=body.pmids)

    results = [
        BatchResult(
            query=r["query"],
            query_type=r["query_type"],
            matched=r["matched"],
            matches=[_to_summary(m) for m in r["matches"]],
        )
        for r in raw_results
    ]
    return BatchResponse(results=results, meta=_meta_response())


# ── External metadata helpers ─────────────────────────────────────────────────

_ENRICH_TIMEOUT = 6.0  # seconds
_ENRICH_HEADERS = {
    "User-Agent": "rwcheck/1.1 (https://github.com/khan-lab/rwcheck; mailto:rwcheck@khanlab.bio)"
}


async def _fetch_crossref(doi: str) -> CrossRefMeta | None:
    """Fetch metadata from the CrossRef REST API for *doi*. Returns None on error."""
    url = f"https://api.crossref.org/works/{doi}"
    try:
        async with httpx.AsyncClient(headers=_ENRICH_HEADERS, timeout=_ENRICH_TIMEOUT) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return None
        msg = r.json().get("message", {})
        titles = msg.get("title") or []
        title = titles[0] if titles else None
        container = msg.get("container-title") or []
        journal = container[0] if container else None
        pub = msg.get("published") or msg.get("published-print") or {}
        parts = (pub.get("date-parts") or [[]])[0]
        year = parts[0] if parts else None
        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (msg.get("author") or [])
        ]
        return CrossRefMeta(
            title=title,
            journal=journal,
            year=year,
            citation_count=msg.get("is-referenced-by-count"),
            type=msg.get("type"),
            authors=authors,
        )
    except Exception:  # noqa: BLE001
        return None


async def _fetch_openalex(doi: str, retraction_year: int | None) -> OpenAlexMeta | None:
    """Fetch metadata from the OpenAlex API for *doi*. Returns None on error."""
    url = f"https://api.openalex.org/works/https://doi.org/{doi}"
    try:
        async with httpx.AsyncClient(headers=_ENRICH_HEADERS, timeout=_ENRICH_TIMEOUT) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return None
        data = r.json()
        cited_by_count = data.get("cited_by_count")
        post_ret: int | None = None
        if retraction_year is not None:
            counts_by_year = data.get("counts_by_year") or []
            post_ret = sum(
                c.get("cited_by_count", 0)
                for c in counts_by_year
                if (c.get("year") or 0) >= retraction_year
            )
        topic_obj = data.get("primary_topic") or {}
        topic = topic_obj.get("display_name")
        oa = (data.get("open_access") or {}).get("oa_status")
        return OpenAlexMeta(
            cited_by_count=cited_by_count,
            cited_by_count_post_retraction=post_ret,
            primary_topic=topic,
            open_access_status=oa,
            openalex_url=data.get("id"),
        )
    except Exception:  # noqa: BLE001
        return None


# ── /enrich/doi ────────────────────────────────────────────────────────────────

@api_v1.get(
    "/enrich/doi/{doi:path}",
    response_model=EnrichResponse,
    tags=["enrich"],
    summary="Enrich a DOI with CrossRef and OpenAlex metadata",
)
@limiter.limit(_RATE_LIMIT)
async def enrich_doi(doi: str, request: Request) -> EnrichResponse:
    """Return Retraction Watch status **plus** metadata from CrossRef and OpenAlex.

    - ``retraction_status`` — local RW database lookup (offline, instant).
    - ``crossref`` — title, journal, publication year, citation count, authors.
    - ``openalex`` — total citations, post-retraction citation count, research topic,
      open-access status.

    Both external sources are queried in parallel and return ``null`` on timeout or
    API error rather than failing the whole request.
    """
    _require_db()
    conn = _get_conn()
    rows = query_by_doi(conn, doi)
    rw_response = MatchResponse(
        query=doi,
        matched=bool(rows),
        matches=[_to_summary(r) for r in rows],
        meta=_meta_response(),
    )

    # Determine retraction year for post-retraction citation calculation
    retraction_year: int | None = None
    if rows:
        ret_date = rows[0].get("retraction_date") or ""
        if ret_date and len(ret_date) >= 4 and ret_date[:4].isdigit():
            retraction_year = int(ret_date[:4])

    crossref_meta, openalex_meta = await asyncio.gather(
        _fetch_crossref(doi),
        _fetch_openalex(doi, retraction_year),
        return_exceptions=False,
    )

    return EnrichResponse(
        doi=doi,
        retraction_status=rw_response,
        crossref=crossref_meta,
        openalex=openalex_meta,
    )


# ── /check/bib ─────────────────────────────────────────────────────────────────

@api_v1.post(
    "/check/bib",
    response_model=BibCheckResponse,
    tags=["lookup"],
    summary="Check a BibTeX file for retractions",
)
@limiter.limit(_RATE_LIMIT)
async def check_bib(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
) -> BibCheckResponse:
    """Upload a ``.bib`` file; returns which entries appear in Retraction Watch."""
    _require_db()
    if not (file.filename or "").lower().endswith(".bib"):
        raise HTTPException(status_code=422, detail="Please upload a .bib file.")
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".bib", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        entries = parse_bib_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    conn = _get_conn()
    results: list[BibCheckEntry] = []
    for entry in entries:
        doi_matches = query_by_doi(conn, entry.doi) if entry.doi else []
        pmid_matches = query_by_pmid(conn, entry.pmid) if entry.pmid else []
        seen: set[int] = set()
        all_matches: list[dict[str, Any]] = []
        for m in doi_matches + pmid_matches:
            if m["record_id"] not in seen:
                seen.add(m["record_id"])
                all_matches.append(m)
        results.append(BibCheckEntry(
            key=entry.key,
            title=entry.title,
            doi=entry.doi_raw,
            pmid=entry.pmid,
            matched=bool(all_matches),
            matches=[_to_summary(m) for m in all_matches],
        ))

    retracted = sum(1 for r in results if r.matched)
    unchecked = sum(1 for r in results if not r.doi and not r.pmid)
    clean = len(results) - retracted - unchecked
    return BibCheckResponse(
        total=len(results),
        retracted=retracted,
        clean=clean,
        unchecked=unchecked,
        results=results,
        meta=_meta_response(),
    )


# ── Report serving (download / share / delete) ────────────────────────────────

def _validate_report_id(report_id: str) -> None:
    if not _re.fullmatch(r"[0-9a-f\-]{36}", report_id):
        raise HTTPException(status_code=400, detail="Invalid report ID")


@api_v1.get("/reports/{report_id}/html", tags=["reports"], summary="View shared HTML report")
async def serve_report_html(report_id: str) -> FileResponse:
    """Serve the self-contained HTML report for a previously uploaded .bib file."""
    _validate_report_id(report_id)
    files = list((_REPORTS_DIR / report_id).glob("*_rwcheck.html"))
    if not files:
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(files[0], media_type="text/html")


@api_v1.get("/reports/{report_id}/zip", tags=["reports"], summary="Download ZIP of all report formats")
async def download_report_zip(report_id: str) -> StreamingResponse:
    """Stream a ZIP archive containing the JSON, Markdown, and HTML reports."""
    _validate_report_id(report_id)
    report_dir = _REPORTS_DIR / report_id
    if not report_dir.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in report_dir.iterdir():
            if f.name != "meta.json":
                zf.write(f, f.name)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=rwcheck_report.zip"},
    )


@api_v1.delete("/reports/{report_id}", tags=["reports"], summary="Delete a report from the server")
async def delete_report(report_id: str) -> dict:
    """Permanently remove all report files for the given report ID."""
    _validate_report_id(report_id)
    report_dir = _REPORTS_DIR / report_id
    if report_dir.exists():
        shutil.rmtree(report_dir)
    return {"deleted": True, "report_id": report_id}


# Mount all versioned routes
app.include_router(api_v1)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"], include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "db_exists": _db_available()})


# ── NiceGUI landing page ────────────────────────────────────────────────────────
# Imported for its @ui.page("/") side-effect; must come after `app` is defined.
from nicegui import ui  # noqa: E402
import rw_api.ui_page  # noqa: F401, E402

ui.run_with(app, title="RWCheck", favicon="🔬", storage_secret="rwcheck-ui")
