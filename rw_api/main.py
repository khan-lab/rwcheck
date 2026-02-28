"""rwcheck REST API — FastAPI application.

Configuration (environment variables)
--------------------------------------
RW_DB_PATH              Path to the SQLite database  (default: data/rw.sqlite)
RW_CSV_URL              URL of the Retraction Watch CSV
RATE_LIMIT              slowapi limit string          (default: 60/minute)
UPDATE_INTERVAL_HOURS   Hours between auto-updates    (default: 24)

Endpoints
---------
GET  /               — HTML landing page
GET  /meta           — dataset provenance
GET  /stats          — aggregate statistics (cached)
GET  /check/doi/{doi}  — ``{doi}`` may contain slashes (path parameter)
GET  /check/pmid/{pmid}
POST /check/batch    — body: {dois: [], pmids: []}
"""

from __future__ import annotations

import base64
import html as _html_mod
import json as _json
import logging
import os
import sqlite3
import tempfile
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import pycountry
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from rw_api.models import (
    BatchRequest,
    BatchResponse,
    BatchResult,
    BibCheckEntry,
    BibCheckResponse,
    MatchResponse,
    MetaResponse,
    RecordSummary,
    StatsResponse,
)
from rwcheck.bib import parse_bib_file
from rwcheck.db import get_meta, get_stats, query_batch, query_by_doi, query_by_pmid
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


# ── Background DB update ───────────────────────────────────────────────────────

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
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    # ── Startup ──────────────────────────────────────────────────────────────
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _update_db_task,
        trigger="interval",
        hours=_UPDATE_INTERVAL_HOURS,
        id="db_update",
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
    return RecordSummary(**{k: row.get(k) for k in RecordSummary.model_fields})


# ── Logo (embedded as base64 data URI so the page works without static files) ──

def _load_logo() -> str:
    logo = Path(__file__).parent.parent / "docs/rwcheck_logo.png"
    if not logo.exists():
        return ""
    return f"data:image/png;base64,{base64.b64encode(logo.read_bytes()).decode()}"


_LOGO_DATA_URI = _load_logo()


# ── Landing page ───────────────────────────────────────────────────────────────

def _h(text: str | None) -> str:
    """HTML-escape a value; return empty string for None/empty."""
    if not text:
        return ""
    return _html_mod.escape(str(text))


_LANDING_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; font-size: 15px;
       line-height: 1.6; color: #2c3e50; background: #f5f6fa; }
a { color: #2980b9; text-decoration: none; }
a:hover { text-decoration: underline; }
.page { max-width: 960px; margin: 0 auto; padding: 0 16px 48px; }

/* ── Top nav ── */
nav {
    background: #BFC9D1; color: #ecf0f1;
    padding: 0 24px; display: flex; align-items: center;
    gap: 24px; height: 52px; margin-bottom: 32px;
}
nav .brand { font-size: 1.25rem; font-weight: 800; color: #fff; letter-spacing: -.02em; }
nav .brand span { color: #2c3e50; }
nav .brand-logo { border-radius: 6px; padding: 2px 8px;
                  display: flex; align-items: center; text-decoration: none; }
nav .brand-logo img { height: 34px; display: block; }
nav .spacer { flex: 1; }
nav a { color: #1a252f; font-size: 0.88rem; }
nav a:hover { color: #fff; text-decoration: none; }

/* ── Hero ── */
.hero { margin-bottom: 32px; }
.hero h1 { font-size: 1.9rem; font-weight: 800; margin-bottom: 8px; }
.hero p { font-size: 1.05rem; color: #555; margin-bottom: 20px; }

/* ── DOI checker ── */
.checker {
    background: #fff; border: 1px solid #dde; border-radius: 10px;
    padding: 18px 20px; max-width: 640px;
}
.checker h3 { font-size: 0.95rem; font-weight: 700; margin-bottom: 10px; color: #444; }
.checker-row { display: flex; gap: 8px; }
.checker input {
    flex: 1; padding: 8px 12px; border: 1px solid #ccc;
    border-radius: 6px; font-size: 0.9rem; font-family: monospace;
}
.checker button {
    padding: 8px 18px; background: #2c3e50; color: #fff;
    border: none; border-radius: 6px; cursor: pointer; font-size: 0.9rem;
}
.checker button:hover { background: #1a252f; }
.check-status { margin-top: 8px; min-height: 20px; font-size: 0.82rem;
                color: #888; font-style: italic; }
.badge-info { color: #888; font-style: italic; }

/* ── Results panel (hidden until a check runs) ── */
#check-results-panel {
    display: none; margin: 20px 0 28px;
    background: #fff; border: 1px solid #ddd; border-radius: 8px;
    overflow: hidden; animation: slideIn 0.18s ease;
}
#check-results-panel.visible { display: block; }
@keyframes slideIn {
    from { opacity: 0; transform: translateY(-5px); }
    to   { opacity: 1; transform: translateY(0); }
}
.results-hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 14px; background: #f8f9fa; border-bottom: 1px solid #eee;
    font-size: 0.82rem; color: #666;
}
.results-hdr code { font-size: 0.8rem; color: #444; }
.results-close {
    background: none; border: none; cursor: pointer;
    color: #aaa; font-size: 1.1rem; line-height: 1; padding: 0 2px;
}
.results-close:hover { color: #333; }
.result-entry { padding: 12px 16px; border-bottom: 1px solid #f0f0f0; }
.result-entry:last-child { border-bottom: none; }
.result-status { font-weight: 700; margin-bottom: 4px; }
.result-entry.ret .result-status { color: #c0392b; }
.result-entry.clean .result-status { color: #27ae60; }
.result-title { font-style: italic; color: #444; margin-bottom: 8px; font-size: 0.9rem; }
.result-kv {
    display: grid; grid-template-columns: 120px 1fr;
    gap: 3px 10px; font-size: 0.82rem;
}
.result-kv .k { color: #888; font-weight: 600; }
.result-kv .v { color: #333; word-break: break-all; }
.result-kv a { color: #2980b9; }
.bib-sum-row {
    padding: 8px 14px; display: flex; gap: 8px; flex-wrap: wrap;
    border-bottom: 1px solid #eee; background: #fafafa;
}
.badge-ok  { display:inline-block; background:#27ae60; color:#fff;
             border-radius:4px; padding:2px 10px; font-weight:700; }
.badge-ret { display:inline-block; background:#c0392b; color:#fff;
             border-radius:4px; padding:2px 10px; font-weight:700; }
.badge-unk { display:inline-block; background:#95a5a6; color:#fff;
             border-radius:4px; padding:2px 9px; font-weight:700; font-size:0.85rem; }

/* ── Stat cards ── */
.stats { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 32px; }
.card {
    flex: 1 1 160px; border-radius: 8px; padding: 16px 20px;
    border-left: 5px solid; background: #fff;
    box-shadow: 0 1px 4px rgba(0,0,0,.07);
}
.card .num { font-size: 1.9rem; font-weight: 800; line-height: 1; }
.card .lbl { font-size: 0.8rem; color: #666; margin-top: 4px; }
.card.c-total  { border-color:#3498db; } .card.c-total  .num { color:#3498db; }
.card.c-jrnl   { border-color:#8e44ad; } .card.c-jrnl   .num { color:#8e44ad; }
.card.c-cntry  { border-color:#16a085; } .card.c-cntry  .num { color:#16a085; }
.card.c-doi    { border-color:#27ae60; } .card.c-doi    .num { color:#27ae60; }
/* ── Last-updated subtitle ── */
.last-updated { font-size: 0.82rem; color: #777; margin-bottom: 10px; }
.last-updated b { color: #555; }

/* ── Two-column checker grid ── */
.check-grid { display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 0; }
.check-col  { flex: 1 1 280px; min-width: 260px; }

/* ── BibTeX uploader ── */
.bib-file-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.bib-file-row input[type=file] {
    flex: 1; padding: 6px 8px; border: 1px solid #ccc;
    border-radius: 6px; font-size: 0.85rem;
}
#bib-result { margin-top: 10px; font-size: 0.85rem; }
.bib-summary { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.bib-badge { display: inline-block; border-radius: 4px; padding: 2px 9px;
             font-weight: 700; font-size: 0.78rem; }
.bib-badge.ret { background: #c0392b; color: #fff; }
.bib-badge.ok  { background: #27ae60; color: #fff; }
.bib-badge.unk { background: #95a5a6; color: #fff; }
.bib-list { max-height: 200px; overflow-y: auto; }
.bib-item { padding: 4px 0; border-bottom: 1px solid #f0f0f0; }
.bib-item.matched { color: #c0392b; }
.bib-item code { font-size: 0.8rem; }

/* ── Section blocks ── */
.section { margin-bottom: 36px; }
.section h2 {
    font-size: 1.1rem; font-weight: 700; margin-bottom: 14px;
    padding-bottom: 6px; border-bottom: 2px solid #e8e8e8; color: #2c3e50;
}

/* ── Chart ── */
.chart-wrap { background:#fff; border:1px solid #ddd; border-radius:8px; padding:20px; }

/* ── Top journals table ── */
.data-table { width:100%; border-collapse:collapse; font-size:0.87rem; background:#fff; }
.data-table thead th {
    background:#f8f9fa; text-align:left; padding:8px 12px;
    font-weight:600; border-bottom:2px solid #ddd;
}
.data-table tbody td { padding:7px 12px; border-bottom:1px solid #f0f0f0; }
.data-table tbody tr:hover td { background:#f8f9fa; }
.rank-bar { display:inline-block; height:10px; background:#c0392b44; border-radius:3px; vertical-align:middle; margin-right:8px; }

/* ── Code examples ── */
.example-block { margin-bottom: 18px; }
.example-block h3 { font-size:0.9rem; font-weight:700; color:#555; margin-bottom:6px; }
.code-wrap { position:relative; }
pre {
    background:#1e2a35; color:#e8f0f8; border-radius:8px;
    padding:14px 16px; font-size:0.82rem; overflow-x:auto;
    line-height:1.5; font-family:ui-monospace,monospace;
}
.copy-btn {
    position:absolute; top:8px; right:8px;
    background:#ffffff22; color:#cde; border:1px solid #ffffff33;
    border-radius:4px; padding:2px 9px; font-size:0.75rem; cursor:pointer;
}
.copy-btn:hover { background:#ffffff44; }

/* ── Cite ── */
.cite-block {
    background:#fff; border:1px solid #ddd; border-radius:8px;
    padding:16px 18px; margin-bottom:14px;
}
.cite-block h3 { font-size:0.9rem; font-weight:700; margin-bottom:8px; color:#444; }
.cite-block pre { background:#f8f9fa; color:#333; border:1px solid #e0e0e0; }

/* ── Tabs ── */
.tab-bar { display:flex; gap:0; border-bottom:2px solid #e8e8e8; margin-bottom:20px; }
.tab-btn {
    padding:8px 20px; background:none; border:none; cursor:pointer;
    font-size:0.9rem; font-weight:600; color:#666;
    border-bottom:3px solid transparent; margin-bottom:-2px;
    transition:color 0.15s,border-color 0.15s;
}
.tab-btn:hover { color:#2c3e50; }
.tab-btn.active { color:#c0392b; border-bottom-color:#c0392b; }
.tab-panel { display:none; }
.tab-panel.active { display:block; }

/* ── Warning banner ── */
.warn-banner {
    background:#fff3cd; border:1px solid #ffc107; border-radius:8px;
    padding:14px 18px; color:#856404; margin-bottom:24px;
}

/* ── Footer ── */
footer {
    margin-top:48px; font-size:0.8rem; color:#aaa;
    border-top:1px solid #e8e8e8; padding-top:14px;
}

@media (max-width: 600px) {
    .stats { flex-direction: column; }
    .checker-row { flex-direction: column; }
}
"""


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


def _render_landing(meta: dict[str, str], stats: dict[str, Any], db_ok: bool) -> str:
    """Return the full HTML string for the API landing page."""
    p: list[str] = []
    w = p.append

    built_at = _h(meta.get("built_at", ""))
    version  = _h(meta.get("dataset_version", ""))
    row_count = int(meta.get("row_count", "0")) if db_ok else 0

    # ── Head ──────────────────────────────────────────────────────────────────
    w("<!DOCTYPE html>")
    w('<html lang="en">')
    w("<head>")
    w('<meta charset="UTF-8">')
    w('<meta name="viewport" content="width=device-width, initial-scale=1">')
    w("<title>RWCheck — Fast Retraction Watch lookup API and CLI</title>")
    w(f"<style>{_LANDING_CSS}</style>")
    if db_ok:
        w(
            '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"'
            ' defer></script>'
        )
        w(
            '<script src="https://cdn.plot.ly/plotly-geo-2.35.0.min.js"'
            ' defer></script>'
        )
    w("</head>")
    w("<body>")

    # ── Nav ───────────────────────────────────────────────────────────────────
    w("<nav>")
    if _LOGO_DATA_URI:
        w(f'<a class="brand-logo" href="/"><img src="{_LOGO_DATA_URI}" alt="RWCheck"></a>')
    else:
        w('<span class="brand">RW<span>Check</span></span>')
    w('<span class="spacer"></span>')
    w('<a href="/docs" target="_blank" rel="noopener">API Docs</a>')
    w('<a href="https://khan-lab.github.io/rwcheck" target="_blank" rel="noopener">CLI Docs</a>')
    w('<a href="/stats">Stats JSON</a>')
    w('<a href="/health">Health</a>')
    w('<a href="https://github.com/khan-lab/rwcheck" target="_blank" rel="noopener">GitHub</a>')
    w("</nav>")

    w('<div class="page">')

    # ── Hero ──────────────────────────────────────────────────────────────────
    w('<section class="hero">')
    w("<h1><b>RWCheck</b>: Fast retractions screening REST API and CLI</h1>")
    w(
        "<p>Retractions are easy to miss and hard to track at scale. "
        "<b>RWCheck</b> provides instant DOI/PMID/.bib screening against a versioned "
        '<a href="https://retractionwatch.com/" target="_blank" rel="noopener">Retraction Watch</a> snapshot '
        "via SQLite, REST API, and CLI—so so researchers, librarians, journals, and pipeline authors can quickly validate references in manuscripts. "
        "The local SQLite database is rebuilt automatically every 24 hours.</p>"
    )
    # Last-updated subtitle + two-column checker grid
    if db_ok:
        _upd = built_at[:10] if built_at else "\u2014"
        w(f'<p class="last-updated">Database last updated: '
          f'<b>{_upd}</b>'
          f' &nbsp;&middot;&nbsp; {row_count:,} records</p>')
    w('<div class="check-grid">')
    # ── Left: DOI / PMID checker ────────────────────────────────────────────
    w('<div class="check-col"><div class="checker">')
    w('<h3>Check a DOI or PMID</h3>')
    w('<div class="checker-row">')
    w('<input id="doi-input" type="text" '
      'placeholder="e.g. 10.1038/nature12345 or 12345678" spellcheck="false">')
    w('<button onclick="checkLookup()">Check</button>')
    w('</div><div id="checker-status" class="check-status"></div>')
    w('</div></div>')
    # ── Right: BibTeX upload ─────────────────────────────────────────────────
    w('<div class="check-col"><div class="checker">')
    w('<h3>Check a .bib file</h3>')
    w('<div class="bib-file-row">')
    w('<input id="bib-input" type="file" accept=".bib">')
    w('<button onclick="checkBib()">Check</button>')
    w('</div><div id="bib-status" class="check-status"></div>')
    w('</div></div>')
    w('</div>')  # .check-grid
    w("</section>")
    # Results panel (hidden by default; populated by JS)
    w('<div id="check-results-panel"></div>')

    # ── Stats / DB-unavailable banner ─────────────────────────────────────────
    if not db_ok:
        w('<div class="warn-banner">')
        w("⏳ <strong>Database is not yet available.</strong> "
          "The background download is in progress — please reload in a moment.")
        w("</div>")
    else:
        total = stats.get("total_records", 0)
        journals = stats.get("total_journals", 0)
        countries = stats.get("total_countries", 0)
        doi_cov = stats.get("doi_coverage", 0)
        doi_pct = f"{doi_cov / total * 100:.0f}%" if total else "—"

        w('<div class="stats">')
        for cls, num, lbl in [
            ("c-total", f"{total:,}", "Total Records"),
            ("c-jrnl",  f"{journals:,}", "Journals"),
            ("c-cntry", f"{countries:,}", "Countries"),
            ("c-doi",   doi_pct, "DOI Coverage"),
        ]:
            w(f'<div class="card {cls}"><div class="num">{num}</div>'
              f'<div class="lbl">{lbl}</div></div>')
        w("</div>")

        # ── Choropleth map ────────────────────────────────────────────────────
        raw_by_country: list[list] = stats.get("by_country", [])
        iso3_locs: list[str] = []
        iso3_counts: list[int] = []
        for entry in raw_by_country:
            code = _map_country_to_iso3(entry[0])
            if code:
                iso3_locs.append(code)
                iso3_counts.append(entry[1])

        if iso3_locs:
            w('<div class="section">')
            w("<h2>Retractions by Country</h2>")
            w(
                '<div id="countryMap" style="height:460px;background:#fff;'
                'border:1px solid #ddd;border-radius:8px;overflow:hidden;"></div>'
            )
            w("</div>")
            _map_js = (
                f"const _mapLocs={_json.dumps(iso3_locs)};\n"
                f"const _mapCounts={_json.dumps(iso3_counts)};\n"
                "window.addEventListener('DOMContentLoaded',function(){\n"
                "  if(typeof Plotly==='undefined'||!_mapLocs.length)return;\n"
                "  Plotly.newPlot('countryMap',[{\n"
                "    type:'choropleth',locationmode:'ISO-3',\n"
                "    locations:_mapLocs,z:_mapCounts,\n"
                "    colorscale:[[0,'#fff5f5'],[0.3,'#e74c3c'],[1,'#641e16']],\n"
                "    colorbar:{title:{text:'Retractions'},thickness:14,len:0.6},\n"
                "    hovertemplate:'<b>%{location}</b><br>%{z:,} retractions<extra></extra>'\n"
                "  }],{\n"
                "    geo:{showframe:false,showcoastlines:true,\n"
                "         coastlinecolor:'#ccc',countrycolor:'#eee',\n"
                "         projection:{type:'natural earth'},bgcolor:'#fff'},\n"
                "    margin:{l:0,r:0,t:10,b:0},paper_bgcolor:'#fff'\n"
                "  },{responsive:true,displayModeBar:false});\n"
                "});"
            )
        else:
            _map_js = ""

        # ── Per-year chart ────────────────────────────────────────────────────
        by_year: list[list] = stats.get("by_year", [])
        if by_year:
            year_labels = _json.dumps([row[0] for row in by_year])
            year_counts = _json.dumps([row[1] for row in by_year])
            w('<div class="section">')
            w("<h2>Retractions per Year</h2>")
            w('<div class="chart-wrap">')
            w('<canvas id="yearChart" height="90"></canvas>')
            w("</div>")
            w("</div>")
            # Script is emitted at bottom — data embedded here via variables
            _year_js = (
                f"const _labels={year_labels};\n"
                f"const _counts={year_counts};\n"
                "window.addEventListener('DOMContentLoaded',function(){\n"
                "  const ctx=document.getElementById('yearChart');\n"
                "  if(!ctx||typeof Chart==='undefined')return;\n"
                "  new Chart(ctx,{type:'bar',data:{\n"
                "    labels:_labels,\n"
                "    datasets:[{label:'Retractions',data:_counts,\n"
                "      backgroundColor:'#c0392b88',borderColor:'#c0392b',borderWidth:1}]\n"
                "  },options:{responsive:true,plugins:{legend:{display:false}},\n"
                "    scales:{y:{beginAtZero:true,ticks:{precision:0}}}}});\n"
                "});"
            )
        else:
            _year_js = ""

        # ── Top 10 journals ───────────────────────────────────────────────────
        top = stats.get("top_journals", [])
        if top:
            max_count = top[0][1] if top else 1
            w('<div class="section">')
            w("<h2>Top 10 Most-Retracted Journals/Conferences</h2>")
            w('<table class="data-table"><thead>')
            w("<tr><th>#</th><th>Journal/Conference</th><th>Retractions</th></tr>")
            w("</thead><tbody>")
            for i, (journal, count) in enumerate(top, 1):
                bar_w = int(count / max_count * 120)
                w(
                    f"<tr><td>{i}</td>"
                    f"<td>{_h(journal)}</td>"
                    f'<td><span class="rank-bar" style="width:{bar_w}px"></span>{count:,}</td>'
                    f"</tr>"
                )
            w("</tbody></table>")
            w("</div>")

        

    # ── API examples ──────────────────────────────────────────────────────────
    _host = _PUBLIC_HOST
    _curl_doi = f"curl '{_host}/check/doi/10.1038/nature12345'"
    _curl_pmid = f"curl '{_host}/check/pmid/12345678'"
    _curl_batch = (
        f"curl -X POST {_host}/check/batch \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d '{\"dois\":[\"10.1038/nature12345\"],\"pmids\":[12345678]}'"
    )
    _py_example = (
        "import httpx\n"
        f"r = httpx.get('{_host}/check/doi/10.1038/nature12345')\n"
        "data = r.json()\n"
        "print(data['matched'], data['matches'])"
    )

    def _code_block(label: str, code: str) -> None:
        safe = _h(code)
        escaped_for_js = code.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        w(f'<div class="example-block"><h3>{label}</h3>')
        w('<div class="code-wrap">')
        w(f"<pre>{safe}</pre>")
        w(f'<button class="copy-btn" onclick="navigator.clipboard.writeText(\'{escaped_for_js}\')">Copy</button>')
        w("</div></div>")

    # ── Strings for CLI / Python API / cite panels ───────────────────────────
    _pip_install = "pip install rwcheck"

    _cli_doi   = "rwcheck doi 10.1038/nature12345"
    _cli_pmid  = "rwcheck pmid 12345678"
    _cli_batch = "rwcheck batch-doi papers.txt --out json | jq '.results[] | select(.matched)'"
    _cli_bib   = "rwcheck batch-bib refs.bib    # writes refs_rwcheck.{md,json,html}"
    _cli_upd   = "rwcheck update                # download latest CSV; skip if unchanged"

    _py_doi = (
        "from rwcheck import check_doi, check_pmid, check_batch\n"
        "\n"
        "# Single DOI lookup — returns dict\n"
        "result = check_doi('10.1038/nature12345', db_path='data/rw.sqlite')\n"
        "if result['matched']:\n"
        "    m = result['matches'][0]\n"
        "    print(m['retraction_nature'], m['retraction_date'])"
    )
    _py_pmid = (
        "result = check_pmid(12345678, db_path='data/rw.sqlite')\n"
        "print(result['matched'], result['matches'])"
    )
    _py_batch = (
        "import json\n"
        "raw = check_batch(\n"
        "    dois=['10.1038/nature12345', '10.9999/test'],\n"
        "    pmids=[12345678],\n"
        "    db_path='data/rw.sqlite',\n"
        ")\n"
        "data = json.loads(raw)\n"
        "retracted = [r for r in data['results'] if r['matched']]"
    )
    _py_env = (
        "import os, rwcheck\n"
        "os.environ['RW_DB_PATH'] = 'data/rw.sqlite'\n"
        "\n"
        "result = rwcheck.check_doi('10.1038/nature12345')"
    )

    _cite_rw = (
        "Retraction Watch. Retraction Watch Database.\n"
        "Center for Scientific Integrity, 2024.\n"
        "Available via CrossRef / GitLab:\n"
        "https://gitlab.com/crossref/retraction-watch-data"
    )
    _cite_tool = (
        "RWCheck: Fast retractions screening REST API and CLI[Software].\n"
        "https://github.com/khan-lab/rwcheck"
    )

    # ── Tabbed section: API / CLI / Python API / Cite ────────────────────────
    w('<div class="section">')
    w('<div class="tab-bar">')
    w('<button class="tab-btn active" onclick="_switchTab(\'tab-api\', this)">REST API Examples</button>')
    w('<button class="tab-btn" onclick="_switchTab(\'tab-cli\', this)">CLI Examples</button>')
    w('<button class="tab-btn" onclick="_switchTab(\'tab-pyapi\', this)">Python API</button>')
    w('<button class="tab-btn" onclick="_switchTab(\'tab-cite\', this)">How to Cite</button>')
    w("</div>")

    # Tab 1 — REST API examples (curl / httpx)
    w('<div id="tab-api" class="tab-panel active">')
    _code_block("Check a DOI (curl)", _curl_doi)
    _code_block("Check a PubMed ID (curl)", _curl_pmid)
    _code_block("Batch lookup (curl)", _curl_batch)
    _code_block("Python (httpx)", _py_example)
    w("</div>")

    # Tab 2 — CLI examples
    w('<div id="tab-cli" class="tab-panel">')
    _code_block("Install", _pip_install)
    _code_block("Check a DOI or PMID", f"{_cli_doi}\n{_cli_pmid}")
    _code_block("Batch check from a file", _cli_batch)
    _code_block("Check a BibTeX file", _cli_bib)
    _code_block("Update the local database", _cli_upd)
    w("</div>")

    # Tab 3 — Python API examples
    w('<div id="tab-pyapi" class="tab-panel">')
    _code_block("Install", _pip_install)
    _code_block("DOI lookup", _py_doi)
    _code_block("PMID lookup", _py_pmid)
    _code_block("Batch lookup", _py_batch)
    _code_block("Using RW_DB_PATH env var (db_path optional)", _py_env)
    w("</div>")

    # Tab 4 — How to cite
    w('<div id="tab-cite" class="tab-panel">')
    w('<div class="cite-block">')
    w("<h3>Retraction Watch Database</h3>")
    safe_rw = _h(_cite_rw)
    esc_rw = _cite_rw.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    w('<div class="code-wrap">')
    w(f"<pre>{safe_rw}</pre>")
    w(f'<button class="copy-btn" onclick="navigator.clipboard.writeText(\'{esc_rw}\')">Copy</button>')
    w("</div></div>")
    w('<div class="cite-block">')
    w("<h3>rwcheck tool</h3>")
    safe_tool = _h(_cite_tool)
    esc_tool = _cite_tool.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    w('<div class="code-wrap">')
    w(f"<pre>{safe_tool}</pre>")
    w(f'<button class="copy-btn" onclick="navigator.clipboard.writeText(\'{esc_tool}\')">Copy</button>')
    w("</div></div>")
    w("</div>")  # tab-cite

    w("</div>")  # .section

    # ── Data source acknowledgement ───────────────────────────────────────────
    w('<div class="section">')
    w("<h2>Data Source</h2>")
    w(
        "<p>The Retraction Watch dataset is maintained by the "
        '<a href="https://retractionwatch.com/" target="_blank" rel="noopener">Center for Scientific Integrity</a> '
        "and distributed via "
        '<a href="https://gitlab.com/crossref/retraction-watch-data" target="_blank" rel="noopener">CrossRef on GitLab</a>. '
        "Please review their "
        '<a href="https://gitlab.com/crossref/retraction-watch-data" target="_blank" rel="noopener">terms of use</a> '
        "before deploying publicly.</p>"
    )
    w("</div>")

    # ── Footer ────────────────────────────────────────────────────────────────
    w("<footer>")
    if db_ok and version:
        w(f"Dataset version <code>{version}</code> · {row_count:,} records · ")
    w(
        'Powered by <a href="https://github.com/khan-lab/rwcheck">rwcheck</a> · '
        'Data from <a href="https://retractionwatch.com/">Retraction Watch</a>'
    )
    w("</footer>")
    w("</div>")  # .page

    # ── Inline scripts ────────────────────────────────────────────────────────
    w("<script>")
    if db_ok and _year_js:
        w(_year_js)
    if db_ok and _map_js:
        w(_map_js)
    w("""
function _switchTab(id, btn) {
  document.querySelectorAll('.tab-panel').forEach(function(p){ p.classList.remove('active'); });
  document.querySelectorAll('.tab-btn').forEach(function(b){ b.classList.remove('active'); });
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}
""")
    # Live checker (DOI + PMID) and BibTeX upload — results panel
    w("""
function _showPanel(html) {
  var p = document.getElementById('check-results-panel');
  p.innerHTML = html;
  p.classList.add('visible');
  p.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function hideResults() {
  var p = document.getElementById('check-results-panel');
  p.classList.remove('visible');
  p.innerHTML = '';
}
function _hdr(label) {
  return '<div class="results-hdr"><span>' + label + '</span>'
       + '<button class="results-close" onclick="hideResults()" title="Close">&#x2715;</button></div>';
}
function _entryKv(m) {
  var kv = '';
  if (m.reason)            kv += '<span class="k">Reason</span><span class="v">' + m.reason + '</span>';
  if (m.journal)           kv += '<span class="k">Journal</span><span class="v">' + m.journal + '</span>';
  if (m.retraction_doi_raw) kv += '<span class="k">Retraction</span><span class="v">'
    + '<a href="https://doi.org/' + m.retraction_doi_raw + '" target="_blank" rel="noopener">'
    + m.retraction_doi_raw + ' \u2197</a></span>';
  if (m.retraction_pmid)   kv += '<span class="k">Ret. PMID</span><span class="v">' + m.retraction_pmid + '</span>';
  if (m.paywalled && m.paywalled !== 'Unknown')
                           kv += '<span class="k">Paywalled</span><span class="v">' + m.paywalled + '</span>';
  return kv ? '<div class="result-kv">' + kv + '</div>' : '';
}
function checkLookup() {
  var q = document.getElementById('doi-input').value.trim();
  if (!q) return;
  var st = document.getElementById('checker-status');
  st.innerHTML = 'Checking\u2026';
  q = q.replace(/^https?:\\/\\/(?:dx\\.)?doi\\.org\\//, '');
  var isPmid = /^\\d+$/.test(q);
  var url = isPmid ? '/check/pmid/' + encodeURIComponent(q)
                   : '/check/doi/'  + encodeURIComponent(q);
  fetch(url)
    .then(function(r){ return r.json(); })
    .then(function(d){
      st.innerHTML = '';
      var lbl = isPmid ? 'PMID \u00b7 <code>' + q + '</code>'
                       : 'DOI \u00b7 <code>' + q + '</code>';
      var html = _hdr(lbl);
      if (d.matched) {
        d.matches.forEach(function(m) {
          var nature = m.retraction_nature || 'Retracted';
          var date   = m.retraction_date ? ' \u00b7 ' + m.retraction_date.substring(0,10) : '';
          html += '<div class="result-entry ret">'
                + '<div class="result-status">&#9888; ' + nature + date + '</div>';
          if (m.title) html += '<div class="result-title">' + m.title + '</div>';
          html += _entryKv(m) + '</div>';
        });
      } else {
        html += '<div class="result-entry clean">'
              + '<div class="result-status">&#10003; Not found in Retraction Watch</div>'
              + '</div>';
      }
      _showPanel(html);
    })
    .catch(function(){
      st.innerHTML = '';
      _showPanel(_hdr('Error') + '<div class="result-entry"><div class="result-status" style="color:#888">'
        + 'Could not reach the database.</div></div>');
    });
}
document.getElementById('doi-input').addEventListener('keydown', function(e){
  if (e.key === 'Enter') checkLookup();
});
function checkBib() {
  var fi = document.getElementById('bib-input');
  if (!fi.files.length) return;
  var st = document.getElementById('bib-status');
  st.innerHTML = 'Uploading\u2026';
  var fd = new FormData();
  fd.append('file', fi.files[0]);
  fetch('/check/bib', { method: 'POST', body: fd })
    .then(function(r){ return r.json(); })
    .then(function(d){
      st.innerHTML = '';
      var fname = fi.files[0].name;
      if (d.detail) {
        _showPanel(_hdr(fname) + '<div class="result-entry"><div class="result-status" style="color:#888">'
          + d.detail + '</div></div>');
        return;
      }
      var html = _hdr(fname + ' \u00b7 ' + d.total + ' entries')
               + '<div class="bib-sum-row">'
               + '<span class="badge-ret">&#9888; ' + d.retracted + ' retracted</span>'
               + '<span class="badge-ok">&#10003; ' + d.clean + ' clean</span>'
               + (d.unchecked ? '<span class="badge-unk">' + d.unchecked + ' no DOI/PMID</span>' : '')
               + '</div>';
      var ret = d.results.filter(function(r){ return r.matched; });
      if (ret.length) {
        ret.forEach(function(r) {
          var m = r.matches[0] || {};
          var nature = m.retraction_nature || 'Retracted';
          var date   = m.retraction_date ? ' \u00b7 ' + m.retraction_date.substring(0,10) : '';
          html += '<div class="result-entry ret">'
                + '<div class="result-status">&#9888; <code>[' + r.key + ']</code> ' + nature + date + '</div>';
          if (r.title) html += '<div class="result-title">' + r.title + '</div>';
          html += _entryKv(m) + '</div>';
        });
      } else {
        html += '<div class="result-entry clean">'
              + '<div class="result-status">&#10003; No retractions found</div></div>';
      }
      _showPanel(html);
    })
    .catch(function(){
      st.innerHTML = '';
      _showPanel(_hdr('Error') + '<div class="result-entry"><div class="result-status" style="color:#888">'
        + 'Upload failed.</div></div>');
    });
}
""")
    w("</script>")
    w("</body></html>")

    return "\n".join(p)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing() -> HTMLResponse:
    """HTML landing page with intro, live DOI checker, stats, and examples."""
    db_ok = _DB_PATH.exists()
    meta: dict[str, str] = _cached_meta() if db_ok else {}
    stats: dict[str, Any] = _cached_stats() if db_ok else {}
    return HTMLResponse(_render_landing(meta, stats, db_ok))


@app.get(
    "/stats",
    response_model=StatsResponse,
    tags=["meta"],
    summary="Dataset statistics",
)
def stats_endpoint() -> StatsResponse:
    """Return aggregate statistics: total records, distinct journals/countries,
    DOI/PMID coverage, retractions per year, and top-10 journals by count.
    Results are cached and cleared on each daily database rebuild.
    """
    _require_db()
    s = _cached_stats()
    return StatsResponse(**s, meta=_meta_response())


@app.get(
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


@app.get(
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


@app.get(
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


@app.post(
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


# ── /check/bib ─────────────────────────────────────────────────────────────────

@app.post(
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


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"], include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "db_exists": _db_available()})
