"""NiceGUI landing page for rwcheck — mounted inside the FastAPI app."""

from __future__ import annotations

import json as _json
import shutil
import tempfile
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from nicegui import events, ui

# In-process helpers — no HTTP round-trip needed
from rw_api.main import (
    _DB_PATH,
    _LOGO_DATA_URI,
    _PUBLIC_HOST,
    _REPORTS_DIR,
    _cached_meta,
    _cached_stats,
    _get_conn,
    _map_country_to_iso3,
)
from rwcheck.bib import parse_bib_file
from rwcheck.db import (
    query_by_doi,
    query_by_pmid,
    query_by_title,
    search_records,
)
from rwcheck.report import BibResult, write_reports

# ── Shared CSS tweaks injected into every page ─────────────────────────────────
_EXTRA_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.rw-hero h1 {
  font-size: 2.1rem;
  font-weight: 700;
  color: #2c3e50;
  margin: 0 0 6px;
  line-height: 1.5; /* good default */
}

/* Mobile: when it wraps, make it feel tighter */
@media (max-width: 480px) {
  .rw-hero h1 {
    font-size: 1.35rem;   /* optional */
    line-height: 1.12;    /* tighter wrap spacing */
    margin-bottom: 2px;   /* less gap below */
  }
}
.rw-hero p  { color: #555; font-size: 0.93rem; margin: 0; }
.stat-num   { font-size: 2rem; font-weight: 800; }
.stat-lbl   { font-size: 0.78rem; color: #888; text-transform: uppercase; letter-spacing: .05em; }
.result-ret { border-left: 4px solid #c0392b; background: #fff5f5; padding: 10px 14px; border-radius: 6px; margin-bottom: 8px; width: 100%; box-sizing: border-box; }
.result-ok  { border-left: 4px solid #27ae60; background: #f0fff4; padding: 10px 14px; border-radius: 6px; width: 100%; box-sizing: border-box; }
.bib-ret    { border-left: 4px solid #c0392b; background: #fff5f5; padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; font-size: 0.85rem;  width: 100%; box-sizing: border-box; }
.bib-ok     { border-left: 4px solid #27ae60; background: #f0fff4; padding: 8px 12px; border-radius: 6px; font-size: 0.85rem;  width: 100%; box-sizing: border-box; }
"""


# ── Sample .bib for the "Try example" button ──────────────────────────────────

_SAMPLE_BIB: Path | None = next(
    (
        p
        for p in [
            Path(__file__).parent.parent / "tests" / "fixtures" / "sample.bib",
            Path.cwd() / "tests" / "fixtures" / "sample.bib",
        ]
        if p.exists()
    ),
    None,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _esc(text: str | None) -> str:
    import html

    return html.escape(str(text)) if text else ""


def _fmt_match(m: dict[str, Any]) -> str:
    """Return a compact HTML string for one RW match record."""
    nature = _esc(m.get("retraction_nature") or "Retracted")
    date = str(m.get("retraction_date") or "")[:10]
    journal = _esc(m.get("journal") or "")
    reason = _esc(m.get("reason") or "")
    title = _esc(m.get("title") or "")
    doi = m.get("original_paper_doi_raw") or m.get("original_paper_doi") or ""
    parts = [f"<b>⚠ {nature}</b>"]
    if date:
        parts.append(date)
    if title:
        parts.append(f"<i>{title}</i>")
    if journal:
        parts.append(f"Journal: {journal}")
    if reason:
        parts.append(f"Reason: {reason}")
    if doi:
        parts.append(
            f'DOI: <a href="https://doi.org/{_esc(doi)}" target="_blank" '
            f'style="color:#2980b9">{_esc(doi)}</a>'
        )
    return "<br>".join(parts)


# ── Core .bib processing (shared by upload handler and "Try example" button) ───


async def _run_bib_check(content: bytes, fname: str, result_col: Any) -> None:
    """Process raw .bib bytes, write reports, and render results into result_col."""
    result_col.clear()
    with result_col:
        ui.spinner(size="sm")
    try:
        with tempfile.NamedTemporaryFile(suffix=".bib", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            entries = parse_bib_file(tmp_path)

            # ── Bulk lookup (deduplicated) ─────────────────────────────────────
            conn = _get_conn()
            doi_map: dict[str, list[dict[str, Any]]] = {}
            pmid_map: dict[int, list[dict[str, Any]]] = {}
            for entry in entries:
                if entry.doi:
                    doi_map[entry.doi] = []
                if entry.pmid:
                    pmid_map[entry.pmid] = []
            for doi in doi_map:
                doi_map[doi] = query_by_doi(conn, doi)
            for pmid in pmid_map:
                pmid_map[pmid] = query_by_pmid(conn, pmid)

            # ── Build BibResult list ───────────────────────────────────────────
            results: list[BibResult] = [
                BibResult(
                    entry=entry,
                    doi_matches=doi_map.get(entry.doi, []) if entry.doi else [],
                    pmid_matches=pmid_map.get(entry.pmid, []) if entry.pmid else [],
                )
                for entry in entries
            ]

            # ── Write reports to a UUID directory ─────────────────────────────
            report_id = str(_uuid.uuid4())
            report_dir = _REPORTS_DIR / report_id
            report_dir.mkdir(parents=True)
            write_reports(results, _cached_meta(), tmp_path, report_dir=report_dir)
            (report_dir / "meta.json").write_text(
                _json.dumps(
                    {
                        "filename": fname,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "report_id": report_id,
                    }
                )
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        # ── Count outcomes ─────────────────────────────────────────────────────
        retracted = sum(1 for r in results if r.matched)
        unchecked = sum(1 for r in results if not r.entry.doi and not r.entry.pmid)
        clean = len(results) - retracted - unchecked
        total = len(results)
        summary = (
            f"{fname} · {total} entries — "
            f"⚠ {retracted} retracted · "
            f"✓ {clean} clean · "
            f"{unchecked} no DOI/PMID"
        )

        result_col.clear()
        with result_col:
            # Inline retracted-entry alerts
            for r in results:
                if r.matched:
                    m0 = r.all_matches[0]
                    nature = _esc(m0.get("retraction_nature") or "Retracted")
                    date = str(m0.get("retraction_date") or "")[:10]
                    reason = _esc(m0.get("reason") or "")
                    doi = m0.get("original_paper_doi_raw") or m0.get("original_paper_doi") or ""
                    ui.html(
                        f'<div class="bib-ret w-full">⚠ <b>[{_esc(r.entry.key)}]</b> '
                        f"{nature} {date}"
                        + (f"<br><i>{_esc(r.entry.title)}</i>" if r.entry.title else "")
                        + (f"<br>Reason: {reason}" if reason else "")
                        + (
                            f'<br>DOI: <a href="https://doi.org/{_esc(doi)}" '
                            f'target="_blank" style="color:#2980b9">{_esc(doi)}</a>'
                            if doi
                            else ""
                        )
                        + "</div>"
                    )

            # Summary line
            if retracted == 0:
                ui.html(
                    f'<div class="bib-ok w-full">✓ <b>No retractions found</b> — {summary}</div>'
                )
            else:
                ui.label(summary).classes("text-sm text-gray-500 mt-1 w-full")

            # ── Action bar ────────────────────────────────────────────────────
            action_row = ui.row().classes("w-full gap-2 items-center mt-3 flex-wrap")
            with action_row:
                _btn_cls = "text-white px-3 py-1 rounded no-underline text-sm"
                ui.link(
                    "⬇ Download ZIP",
                    f"/api/v1/reports/{report_id}/zip",
                    new_tab=True,
                ).classes(f"bg-blue-600 hover:bg-blue-700 {_btn_cls}")

                ui.link(
                    "👁 View report",
                    f"/api/v1/reports/{report_id}/html",
                    new_tab=True,
                ).classes(f"bg-teal-600 hover:bg-teal-700 {_btn_cls}")

                share_url = f"{_PUBLIC_HOST}/api/v1/reports/{report_id}/html"

                async def _copy_link(url: str = share_url) -> None:
                    await ui.run_javascript(f"navigator.clipboard.writeText({_json.dumps(url)})")
                    ui.notify("Share link copied!", type="positive")

                ui.button("🔗 Copy share link", on_click=_copy_link).props("flat").classes(
                    "text-sm"
                )

                def _delete(rid: str = report_id, row: Any = action_row) -> None:
                    shutil.rmtree(_REPORTS_DIR / rid, ignore_errors=True)
                    row.delete()
                    ui.notify("Report deleted from server.", type="warning")

                ui.button("🗑 Delete", color="red", on_click=_delete).props("flat").classes(
                    "text-sm"
                )

    except Exception as exc:  # noqa: BLE001
        result_col.clear()
        with result_col:
            ui.label(f"Error: {exc}").classes("text-red-600 text-sm")


# ── Page definition ────────────────────────────────────────────────────────────


@ui.page("/")
async def index() -> None:
    ui.add_head_html(f"<style>{_EXTRA_CSS}</style>")

    db_ok = _DB_PATH.exists()
    stats: dict[str, Any] = _cached_stats() if db_ok else {}
    meta: dict[str, str] = _cached_meta() if db_ok else {}

    # ── Header / nav ──────────────────────────────────────────────────────────
    with ui.header().classes("bg-[#F3F4F6] px-6 py-3 flex items-center gap-6"):
        if _LOGO_DATA_URI:
            ui.html(
                f'<a href="/"><img src="{_LOGO_DATA_URI}" style="height:34px;display:block;" alt="RWCheck"></a>'
            )
        else:
            ui.label("RWCheck").classes("text-xl font-bold")
        ui.space()
        ui.link("REST API", "/docs", new_tab=True).classes(
            "text-[#2c3e50] font-semibold no-underline hover:underline"
        )
        ui.link("CLI Docs", "https://khan-lab.github.io/rwcheck", new_tab=True).classes(
            "text-[#2c3e50] font-semibold no-underline hover:underline"
        )
        ui.link("Source Code", "https://github.com/khan-lab/rwcheck", new_tab=True).classes(
            "text-[#2c3e50] font-semibold no-underline hover:underline"
        )

    # ── Main content ──────────────────────────────────────────────────────────
    with ui.column().classes("w-full max-w-4xl mx-auto px-4 py-6 gap-6"):
        # ── Hero ──────────────────────────────────────────────────────────────
        with ui.element("div").classes("rw-hero"):
            ui.html("<h1>Catch retracted papers before they land in your citations </h1>")
            ui.html(
                "<p><b>RWCheck</b> provides fast screening of DOIs, PubMed IDs, and BibTeX files "
                "against an updated version of the Retraction Watch snapshot. Use this or offline <b>Web App</b> for quick checks, "
                "the <a href='https://khan-lab.github.io/rwcheck' target='_blank'><b>CLI</b></a> to scan at scale, or the <a href='/docs' target='_blank'><b>REST API</b></a> to automate screening.</p<br>"
            )
            if db_ok:
                built = meta.get("built_at", "")[:10] or "—"
                rows = int(meta.get("row_count", 0))
                ui.label(f"Database last updated: {built} · {rows:,} records").classes(
                    "text-xs text-gray-400 mt-1"
                )

        # ── BibTeX upload ─────────────────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("Check a .bib file").classes("text-base font-bold")
            ui.label(
                "Upload your BibTeX bibliography to screen every reference (by DOI or PMID) "
                "against the Retraction Watch database."
            ).classes("text-sm text-gray-500 mb-2")

            bib_result = ui.column().classes("w-full gap-2")

            async def handle_bib(e: events.UploadEventArguments) -> None:
                content = await e.file.read()
                await _run_bib_check(content, e.file.name, bib_result)

            ui.upload(
                label="Drop a .bib file here or click to browse",
                on_upload=handle_bib,
                auto_upload=True,
            ).props('accept=".bib" flat bordered').classes("w-full")

            if _SAMPLE_BIB:

                async def _try_example() -> None:
                    await _run_bib_check(_SAMPLE_BIB.read_bytes(), "sample.bib", bib_result)

                with ui.row().classes("w-full justify-end mt-1"):
                    ui.button(
                        "Try with sample.bib →",
                        on_click=_try_example,
                    ).props("flat").classes("text-xs text-blue-600")

        # ── Single reference checker ───────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("Check a single reference").classes("text-base font-bold")
            ui.label(
                "Enter a DOI, PubMed ID, or article title. Paste a doi.org URL and it will be normalised automatically."
            ).classes("text-sm text-gray-500 mb-2")

            _placeholders = {
                "DOI": "e.g. 10.1038/nature12345",
                "PMID": "e.g. 12345678",
                "Title": "e.g. Climate model validation study",
            }
            mode = ui.radio(["DOI", "PMID", "Title"], value="DOI").props("inline")
            with ui.row().classes("w-full gap-2 items-center"):
                query_input = ui.input(placeholder=_placeholders["DOI"]).classes("flex-1")
                check_btn = ui.button("Check", color="blue")

            mode.on_value_change(
                lambda e: query_input.props(f'placeholder="{_placeholders[e.value]}"')
            )

            check_result = ui.column().classes("w-full mt-2")

            async def do_check() -> None:
                q = query_input.value.strip()
                if not q:
                    return
                check_result.clear()
                with check_result:
                    ui.spinner(size="sm")
                try:
                    conn = _get_conn()
                    m_val = mode.value
                    if m_val == "DOI":
                        # strip doi.org prefix
                        import re

                        q = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", q)
                        from rwcheck.normalize import normalize_doi

                        doi = normalize_doi(q)
                        rows = query_by_doi(conn, doi) if doi else []
                        label = f"DOI: {q}"
                    elif m_val == "PMID":
                        from rwcheck.normalize import normalize_pmid

                        pmid = normalize_pmid(q)
                        rows = query_by_pmid(conn, pmid) if pmid else []
                        label = f"PMID: {q}"
                    else:
                        rows = query_by_title(conn, q)
                        label = f"Title: {q}"

                    check_result.clear()
                    with check_result:
                        if rows:
                            for row in rows:
                                ui.html(
                                    f'<div class="result-ret">'
                                    f"<b>{_esc(label)}</b><br>"
                                    + _fmt_match(row)
                                    + (
                                        "<br><small>⚠ Title match — verify with DOI or PMID</small>"
                                        if m_val == "Title"
                                        else ""
                                    )
                                    + "</div>"
                                )
                        else:
                            ui.html(
                                f'<div class="result-ok">✓ <b>Not found in Retraction Watch</b><br>'
                                f"<small>{_esc(label)}</small></div>"
                            )
                except Exception as exc:  # noqa: BLE001
                    check_result.clear()
                    with check_result:
                        ui.label(f"Error: {exc}").classes("text-red-600 text-sm")

            check_btn.on_click(do_check)
            query_input.on("keydown.enter", do_check)

        # ── Stats cards ───────────────────────────────────────────────────────
        if db_ok and stats:
            total = stats.get("total_records", 0)
            doi_cov = stats.get("doi_coverage", 0)
            doi_pct = f"{doi_cov / total * 100:.0f}%" if total else "—"
            stat_items = [
                ("#c0392b", f"{total:,}", "Total Records"),
                ("#2980b9", f"{stats.get('total_journals', 0):,}", "Journals"),
                ("#27ae60", f"{stats.get('total_countries', 0):,}", "Countries"),
                ("#d26f18", f"{stats.get('total_authors', 0):,}", "Authors"),
                ("#f3c370", doi_pct, "DOI Coverage"),
            ]
            with ui.row().classes("w-full gap-4 flex-wrap"):
                for color, num, lbl in stat_items:
                    with ui.card().classes("flex-1 min-w-[140px] text-center py-4"):
                        ui.label(num).classes("stat-num").style(f"color:{color}")
                        ui.label(lbl).classes("stat-lbl")

        # ── Retractions per year (ECharts bar) ────────────────────────────────
        by_year: list[list] = stats.get("by_year", []) if db_ok else []
        if by_year:
            with ui.card().classes("w-full"):
                ui.label("Retractions per Year").classes("text-base font-bold mb-2")
                labels = [str(r[0]) for r in by_year]
                counts = [r[1] for r in by_year]
                ui.echart(
                    {
                        "tooltip": {"trigger": "axis"},
                        "xAxis": {"type": "category", "data": labels},
                        "yAxis": {"type": "value"},
                        "series": [
                            {
                                "type": "bar",
                                "data": counts,
                                "itemStyle": {
                                    "color": "#c0392b88",
                                    "borderColor": "#c0392b",
                                    "borderWidth": 1,
                                },
                                "name": "Retractions",
                            }
                        ],
                        "grid": {"left": "5%", "right": "2%", "bottom": "5%", "containLabel": True},
                    }
                ).classes("w-full h-72")

        # ── Retractions by country (Plotly choropleth) ────────────────────────
        raw_by_country: list[list] = stats.get("by_country", []) if db_ok else []
        iso3_locs, iso3_counts = [], []
        for entry in raw_by_country:
            code = _map_country_to_iso3(entry[0])
            if code:
                iso3_locs.append(code)
                iso3_counts.append(entry[1])

        if iso3_locs:
            with ui.card().classes("w-full"):
                ui.label("Retractions by Country").classes("text-base font-bold mb-2")
                fig = go.Figure(
                    go.Choropleth(
                        locations=iso3_locs,
                        z=iso3_counts,
                        locationmode="ISO-3",
                        colorscale=[[0, "#fff5f5"], [0.3, "#e74c3c"], [1, "#641e16"]],
                        colorbar={"title": {"text": "Retractions"}, "thickness": 14, "len": 0.6},
                        hovertemplate="<b>%{location}</b><br>%{z:,} retractions<extra></extra>",
                    )
                )
                fig.update_layout(
                    margin={"l": 0, "r": 0, "t": 10, "b": 0},
                    paper_bgcolor="#fff",
                    geo={
                        "showframe": False,
                        "showcoastlines": True,
                        "coastlinecolor": "#ccc",
                        "countrycolor": "#eee",
                        "projection": {"type": "natural earth"},
                        "bgcolor": "#fff",
                    },
                    height=420,
                )
                ui.plotly(fig).classes("w-full")

        # ── Search / filter ───────────────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("Search the database").classes("text-base font-bold")
            ui.label(
                "Filter by journal, author, country, reason, or year. All filters are ANDed."
            ).classes("text-sm text-gray-500 mb-2")

            with ui.row().classes("w-full gap-2 flex-wrap"):
                journal_in = ui.input(placeholder="Journal (exact)").classes("flex-1 min-w-[180px]")
                author_in = ui.input(placeholder="Author (partial)").classes("flex-1 min-w-[180px]")
                country_in = ui.input(placeholder="Country (partial)").classes(
                    "flex-1 min-w-[160px]"
                )
                reason_in = ui.input(placeholder="Reason (partial)").classes("flex-1 min-w-[160px]")
                year_in = ui.input(placeholder="Year (e.g. 2020)").classes("w-28")

            search_btn = ui.button("Search", color="blue").classes("mt-1")
            search_result = ui.column().classes("w-full mt-2 gap-1")

            async def do_search() -> None:
                journal = journal_in.value.strip() or None
                author = author_in.value.strip() or None
                country = country_in.value.strip() or None
                reason = reason_in.value.strip() or None
                year_raw = year_in.value.strip()
                year: int | None = int(year_raw) if year_raw.isdigit() else None
                if not any([journal, author, country, reason, year]):
                    ui.notify("Enter at least one filter", type="warning")
                    return
                search_result.clear()
                with search_result:
                    ui.spinner(size="sm")
                try:
                    conn = _get_conn()
                    total_found, records = search_records(
                        conn,
                        limit=100,
                        offset=0,
                        journal=journal,
                        author=author,
                        country=country,
                        reason=reason,
                        year=year,
                    )
                    search_result.clear()
                    with search_result:
                        ui.label(f"{total_found:,} results (showing up to 100)").classes(
                            "text-sm text-gray-500 w-full"
                        )
                        cols = [
                            {"name": "title", "label": "Title", "field": "title", "align": "left"},
                            {
                                "name": "journal",
                                "label": "Journal",
                                "field": "journal",
                                "align": "left",
                            },
                            {
                                "name": "nature",
                                "label": "Nature",
                                "field": "nature",
                                "align": "left",
                            },
                            {"name": "date", "label": "Date", "field": "date", "align": "left"},
                            {
                                "name": "reason",
                                "label": "Reason",
                                "field": "reason",
                                "align": "left",
                            },
                            {"name": "doi", "label": "DOI", "field": "doi", "align": "left"},
                        ]
                        rows_data = [
                            {
                                "title": r.get("title") or "",
                                "journal": r.get("journal") or "",
                                "nature": r.get("retraction_nature") or "",
                                "date": str(r.get("retraction_date") or "")[:10],
                                "reason": r.get("reason") or "",
                                "doi": r.get("original_paper_doi_raw")
                                or r.get("original_paper_doi")
                                or "",
                            }
                            for r in records
                        ]
                        tbl = ui.table(columns=cols, rows=rows_data, row_key="title").classes(
                            "w-full text-sm"
                        )
                        tbl.add_slot(
                            "body-cell-doi",
                            """
                            <q-td :props="props">
                              <a v-if="props.value"
                                 :href="'https://doi.org/' + props.value"
                                 target="_blank"
                                 style="color:#2980b9;word-break:break-all">
                                {{ props.value }}
                              </a>
                              <span v-else style="color:#aaa">—</span>
                            </q-td>
                        """,
                        )
                except Exception as exc:  # noqa: BLE001
                    search_result.clear()
                    with search_result:
                        ui.label(f"Error: {exc}").classes("text-red-600 text-sm")

            search_btn.on_click(do_search)

        # ── Code examples ─────────────────────────────────────────────────────
        _host = _PUBLIC_HOST
        examples = {
            "REST API": [
                ("Check a DOI (curl)", f"curl '{_host}/check/doi/10.1038/nature12345'"),
                ("Check a PMID (curl)", f"curl '{_host}/check/pmid/12345678'"),
                (
                    "Batch lookup (curl)",
                    f"curl -X POST {_host}/check/batch \\\n"
                    "  -H 'Content-Type: application/json' \\\n"
                    '  -d \'{"dois":["10.1038/nature12345"],"pmids":[12345678]}\'',
                ),
            ],
            "CLI": [
                ("Install", "pip install rwcheck"),
                ("Check a DOI or PMID", "rwcheck doi 10.1038/nature12345\nrwcheck pmid 12345678"),
                (
                    "Check a BibTeX file",
                    "rwcheck batch-bib refs.bib    # writes refs_rwcheck.{md,json,html}",
                ),
                ("Update the local database", "rwcheck update"),
            ],
            "Python API": [
                ("Install", "pip install rwcheck"),
                (
                    "DOI lookup",
                    "from rwcheck import check_doi\n"
                    "result = check_doi('10.1038/nature12345', db_path='data/rw.sqlite')\n"
                    "if result['matched']:\n"
                    "    m = result['matches'][0]\n"
                    "    print(m['retraction_nature'], m['retraction_date'])",
                ),
            ],
        }
        with ui.card().classes("w-full"):
            ui.label("Code Examples").classes("text-base font-bold mb-2")
            with ui.tabs().classes("w-full") as tabs:
                tab_objs = {name: ui.tab(name) for name in examples}
            with ui.tab_panels(tabs, value=list(examples.keys())[0]).classes("w-full"):
                for name, snippets in examples.items():
                    with ui.tab_panel(tab_objs[name]):
                        for label, code in snippets:
                            ui.label(label).classes("text-xs font-semibold text-gray-500 mt-3 mb-1")
                            with ui.element("div").classes("w-full"):
                                ui.code(code, language="bash").classes("w-full text-sm")

        # ── Data source ───────────────────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("Data Source").classes("text-base font-bold mb-1")
            ui.html(
                "The Retraction Watch dataset is maintained by the "
                '<a href="https://retractionwatch.com/" target="_blank" class="text-blue-600">Center for Scientific Integrity</a> '
                "and distributed via "
                '<a href="https://gitlab.com/crossref/retraction-watch-data" target="_blank" class="text-blue-600">CrossRef on GitLab</a>. '
                "Please review their terms of use before deploying publicly."
            ).classes("text-sm text-gray-600")

    # ── Footer ─────────────────────────────────────────────────────────────────
    with ui.footer().classes("bg-gray-100 text-gray-500 text-xs text-center py-3"):
        if db_ok and meta.get("dataset_version"):
            v = meta["dataset_version"]
            rows = int(meta.get("row_count", 0))
            ui.label(f"Dataset version {v} · {rows:,} records · ")
        ui.html(
            '<p> <b>RW</b>Check is open source software built by the <a href="https://khan-lab.github.io" target="_blank">'
            '<b>Khan Lab</b></a> and powered by the <a href="https://retractionwatch.com/" class="underline">Retraction Watch</a> '
            'dataset made publically available by <a href="https://gitlab.com/crossref/retraction-watch-data" class="underline">CrossRef</a>.</p>'
        )
