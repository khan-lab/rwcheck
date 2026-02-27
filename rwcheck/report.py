"""Report generation for the ``batch-bib`` command.

Produces three output files per run:
  - ``<stem>_rwcheck.json`` — machine-readable full results.
  - ``<stem>_rwcheck.md``  — human-readable Markdown report.
  - ``<stem>_rwcheck.html`` — self-contained HTML report (no external deps).
"""

from __future__ import annotations

import base64
import html
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rwcheck.bib import BibEntry
from rwcheck.normalize import normalize_doi

# ── Logo (base64 data URI for self-contained HTML output) ─────────────────────

def _load_logo() -> str:
    logo = Path(__file__).parent.parent / "rwcheck_logo.png"
    if not logo.exists():
        return ""
    return f"data:image/png;base64,{base64.b64encode(logo.read_bytes()).decode()}"


_LOGO_DATA_URI = _load_logo()


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class BibResult:
    """Combines a parsed :class:`BibEntry` with Retraction Watch lookup results."""

    entry: BibEntry
    doi_matches: list[dict[str, Any]] = field(default_factory=list)
    pmid_matches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        """True if any retraction record was found."""
        return bool(self.doi_matches or self.pmid_matches)

    @property
    def all_matches(self) -> list[dict[str, Any]]:
        """Deduplicated union of DOI and PMID match records."""
        seen: set[int] = set()
        result: list[dict[str, Any]] = []
        for m in self.doi_matches + self.pmid_matches:
            rid = m.get("record_id")
            if rid not in seen:
                seen.add(rid)
                result.append(m)
        return result

    @property
    def checkable(self) -> bool:
        """True if the entry has a DOI or PMID to look up."""
        return self.entry.checkable


# ── JSON report ───────────────────────────────────────────────────────────────

def generate_json_report(
    results: list[BibResult],
    meta: dict[str, str],
    input_path: Path,
) -> str:
    """Return a JSON string with the full batch-bib results.

    Parameters
    ----------
    results:
        One :class:`BibResult` per BibTeX entry.
    meta:
        Dataset metadata from the ``meta`` table.
    input_path:
        Path to the source ``.bib`` file (used in the report header).
    """
    n_retracted = sum(1 for r in results if r.matched)
    n_unchecked = sum(1 for r in results if not r.checkable)
    n_clean = len(results) - n_retracted - n_unchecked

    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "input_file": input_path.name,
        "meta": meta,
        "summary": {
            "total": len(results),
            "retracted": n_retracted,
            "clean": n_clean,
            "unchecked": n_unchecked,
        },
        "results": [
            {
                "key": r.entry.key,
                "entry_type": r.entry.entry_type,
                "title": r.entry.title,
                "authors": r.entry.authors,
                "year": r.entry.year,
                "journal": r.entry.journal,
                "doi": r.entry.doi,
                "doi_raw": r.entry.doi_raw,
                "pmid": r.entry.pmid,
                "checkable": r.checkable,
                "matched": r.matched,
                "matches": r.all_matches,
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ── Markdown report ───────────────────────────────────────────────────────────

def _md_escape(text: str | None) -> str:
    """Escape pipe characters for use inside Markdown table cells."""
    if not text:
        return ""
    return str(text).replace("|", "\\|")


def generate_md_report(
    results: list[BibResult],
    meta: dict[str, str],
    input_path: Path,
) -> str:
    """Return a Markdown string suitable for use as a standalone report file.

    Parameters
    ----------
    results:
        One :class:`BibResult` per BibTeX entry.
    meta:
        Dataset metadata from the ``meta`` table.
    input_path:
        Path to the source ``.bib`` file (used in the report header).
    """
    retracted = [r for r in results if r.matched]
    clean = [r for r in results if r.checkable and not r.matched]
    unchecked = [r for r in results if not r.checkable]

    lines: list[str] = []
    a = lines.append  # shorthand

    # ── Header ────────────────────────────────────────────────────────────────
    a(f"# Retraction Watch Report — {input_path.name}")
    a("")
    a(
        f"**Dataset version**: `{meta.get('dataset_version', 'n/a')}`  "
        f"**Rows**: {int(meta.get('row_count', 0)):,}  "
        f"**Built**: {meta.get('built_at', 'n/a')}"
    )
    a(f"**Generated**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    a("")

    # ── Summary ───────────────────────────────────────────────────────────────
    a("## Summary")
    a("")
    a("| Category | Count |")
    a("|---|---|")
    a(f"| Total references | **{len(results)}** |")
    a(f"| ⚠️ Retracted | **{len(retracted)}** |")
    a(f"| ✓ Not found in Retraction Watch | {len(clean)} |")
    a(f"| ⬜ No DOI/PMID (unchecked) | {len(unchecked)} |")
    a("")

    # ── Retracted ─────────────────────────────────────────────────────────────
    if retracted:
        a(f"## ⚠️ Retracted References ({len(retracted)})")
        a("")
        for r in retracted:
            e = r.entry
            year_str = f" ({e.year})" if e.year else ""
            journal_str = f" — *{e.journal}*" if e.journal else ""
            a(f"### [{e.key}] {e.short_author}{year_str}{journal_str}")
            a("")
            if e.title:
                a(f"**Title**: {e.title}")
                a("")
            a("| Field | Value |")
            a("|---|---|")
            if e.doi_raw:
                a(f"| **DOI (original)** | `{_md_escape(e.doi_raw)}` |")
            if e.pmid:
                a(f"| **PMID** | `{e.pmid}` |")
            for m in r.all_matches:
                a(f"| **Retraction nature** | {_md_escape(m.get('retraction_nature'))} |")
                a(f"| **Reason** | {_md_escape(m.get('reason'))} |")
                a(f"| **Retraction date** | {_md_escape(m.get('retraction_date'))} |")
                if m.get("retraction_doi_raw"):
                    a(f"| **Retraction notice DOI** | `{_md_escape(m.get('retraction_doi_raw'))}` |")
                if m.get("retraction_pmid"):
                    a(f"| **Retraction PMID** | `{m.get('retraction_pmid')}` |")
                a(f"| **Journal** | {_md_escape(m.get('journal'))} |")
                a(f"| **Paywalled** | {_md_escape(m.get('paywalled'))} |")
                # Only show first match details in the table; list extras below.
                break
            if len(r.all_matches) > 1:
                a("")
                a(f"> ⚠️ This entry matched **{len(r.all_matches)}** Retraction Watch records.")
            a("")
    else:
        a("## ⚠️ Retracted References")
        a("")
        a("*No retracted references found.*")
        a("")

    # ── Clean ─────────────────────────────────────────────────────────────────
    if clean:
        a(f"## ✓ Clean References ({len(clean)})")
        a("")
        a("| Key | Authors | Year | Journal | DOI |")
        a("|---|---|---|---|---|")
        for r in clean:
            e = r.entry
            doi_cell = f"`{_md_escape(e.doi_raw)}`" if e.doi_raw else (f"PMID:{e.pmid}" if e.pmid else "—")
            a(
                f"| {_md_escape(e.key)} "
                f"| {_md_escape(e.short_author)} "
                f"| {_md_escape(e.year)} "
                f"| {_md_escape(e.journal)} "
                f"| {doi_cell} |"
            )
        a("")

    # ── Unchecked ─────────────────────────────────────────────────────────────
    if unchecked:
        a(f"## ⬜ Unchecked — No DOI or PMID ({len(unchecked)})")
        a("")
        a("| Key | Entry type | Title |")
        a("|---|---|---|")
        for r in unchecked:
            e = r.entry
            a(
                f"| {_md_escape(e.key)} "
                f"| {_md_escape(e.entry_type)} "
                f"| {_md_escape(e.title)} |"
            )
        a("")

    a("---")
    a("")
    a(
        "*Report generated by [rwcheck](https://github.com/your-org/rwcheck). "
        "Data from [Retraction Watch](https://retractionwatch.com/).*"
    )

    return "\n".join(lines)


# ── HTML report ───────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 15px; line-height: 1.6;
    color: #2c3e50; background: #f5f6fa;
}
.page { max-width: 960px; margin: 0 auto; padding: 24px 16px; }
header { margin-bottom: 28px; }
header img { height: 52px; display: block; margin-bottom: 10px; }
header h1 { font-size: 1.6rem; font-weight: 700; margin-bottom: 6px; }
.meta-bar {
    font-size: 0.82rem; color: #555;
    background: #fff; border: 1px solid #ddd;
    border-radius: 6px; padding: 8px 14px;
    display: flex; flex-wrap: wrap; gap: 16px;
}
.meta-bar span b { color: #2c3e50; }

/* ── Summary cards ── */
.summary { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 32px; }
.card {
    flex: 1 1 180px; border-radius: 8px; padding: 16px 20px;
    border-left: 5px solid; background: #fff;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
}
.card .num { font-size: 2.2rem; font-weight: 800; line-height: 1; }
.card .lbl { font-size: 0.82rem; color: #666; margin-top: 4px; }
.card.total   { border-color: #3498db; } .card.total   .num { color: #3498db; }
.card.ret     { border-color: #c0392b; } .card.ret     .num { color: #c0392b; }
.card.clean   { border-color: #27ae60; } .card.clean   .num { color: #27ae60; }
.card.unchk   { border-color: #95a5a6; } .card.unchk   .num { color: #95a5a6; }

/* ── Sections ── */
.section { margin-bottom: 32px; }
.section h2 {
    font-size: 1.1rem; font-weight: 700;
    padding: 10px 14px; border-radius: 6px 6px 0 0;
    border: 1px solid #ddd; border-bottom: none;
}
.section.ret h2  { background: #fdf2f2; color: #c0392b; border-color: #f5c6cb; }
.section.clean h2 { background: #f2fdf5; color: #1e8449; border-color: #c3e6cb; }
.section.unchk h2 { background: #f8f9fa; color: #555;    border-color: #dee2e6; }

/* ── Retracted entries (details/summary) ── */
.ret-entry {
    border: 1px solid #f5c6cb; border-top: none;
    background: #fff; overflow: hidden;
}
.ret-entry:last-child { border-radius: 0 0 6px 6px; }
details > summary {
    padding: 10px 16px; cursor: pointer;
    background: #fff9f9; font-weight: 600;
    list-style: none; display: flex; align-items: center; gap: 8px;
    border-top: 1px solid #f5c6cb;
}
details > summary::before { content: "▶"; font-size: 0.7rem; color: #c0392b; }
details[open] > summary::before { content: "▼"; }
details > summary .badge {
    display: inline-block; background: #c0392b; color: #fff;
    border-radius: 4px; font-size: 0.72rem; padding: 1px 7px;
    font-weight: 700; text-transform: uppercase; letter-spacing: .03em;
}
.entry-body { padding: 12px 16px 16px; }
.entry-body .entry-title { font-style: italic; color: #444; margin-bottom: 10px; }
.kv-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.kv-table tr:nth-child(even) td { background: #fafafa; }
.kv-table td { padding: 5px 10px; border: 1px solid #eee; }
.kv-table td:first-child { font-weight: 600; width: 200px; color: #555; }
.kv-table code { font-size: 0.84rem; background: #f4f4f4; padding: 1px 5px; border-radius: 3px; }
.multi-match-note {
    margin-top: 10px; padding: 8px 12px; border-radius: 4px;
    background: #fff3cd; border: 1px solid #ffc107; font-size: 0.85rem;
}

/* ── Tables (clean / unchecked) ── */
.data-table-wrap {
    border: 1px solid #ddd; border-top: none;
    border-radius: 0 0 6px 6px; overflow: hidden; background: #fff;
}
.data-table { width: 100%; border-collapse: collapse; font-size: 0.87rem; }
.data-table thead th {
    background: #f8f9fa; text-align: left;
    padding: 8px 12px; font-weight: 600; border-bottom: 2px solid #ddd;
}
.data-table tbody td { padding: 7px 12px; border-bottom: 1px solid #f0f0f0; }
.data-table tbody tr:hover td { background: #f8f9fa; }
.data-table code { font-size: 0.82rem; background: #f4f4f4; padding: 1px 5px; border-radius: 3px; }
.empty { padding: 16px; color: #888; font-style: italic;
         border: 1px solid #ddd; border-top: none;
         border-radius: 0 0 6px 6px; background: #fff; }

/* ── Footer ── */
footer { margin-top: 40px; font-size: 0.8rem; color: #aaa; border-top: 1px solid #eee; padding-top: 12px; }
footer a { color: #aaa; }

@media print {
    body { background: #fff; }
    .page { max-width: 100%; padding: 0; }
    details[open] { page-break-inside: avoid; }
}
"""

def _h(text: str | None) -> str:
    """HTML-escape a value (returns empty string for None/empty)."""
    if not text:
        return ""
    return html.escape(str(text))


def _doi_link(doi: str | None) -> str:
    """Return an anchor tag for a DOI, or a plain code tag if no DOI."""
    if not doi:
        return ""
    url = f"https://doi.org/{html.escape(doi)}"
    return f'<a href="{url}" target="_blank" rel="noopener"><code>{_h(doi)}</code></a>'


def generate_html_report(
    results: list[BibResult],
    meta: dict[str, str],
    input_path: Path,
) -> str:
    """Return a self-contained HTML string for the batch-bib report.

    The file includes all styles inline (no CDN, no JavaScript) so it can be
    opened directly in any browser without an internet connection.

    Parameters
    ----------
    results:
        One :class:`BibResult` per BibTeX entry.
    meta:
        Dataset metadata from the ``meta`` table.
    input_path:
        Path to the source ``.bib`` file (used in the report title).
    """
    retracted = [r for r in results if r.matched]
    clean = [r for r in results if r.checkable and not r.matched]
    unchecked = [r for r in results if not r.checkable]

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    row_count = int(meta.get("row_count", 0))
    title = f"Retraction Watch Report — {_h(input_path.name)}"

    parts: list[str] = []
    w = parts.append

    # ── Boilerplate ───────────────────────────────────────────────────────────
    w("<!DOCTYPE html>")
    w('<html lang="en">')
    w("<head>")
    w('<meta charset="UTF-8">')
    w('<meta name="viewport" content="width=device-width, initial-scale=1">')
    w(f"<title>{title}</title>")
    w(f"<style>{_CSS}</style>")
    w("</head>")
    w('<body><div class="page">')

    # ── Header ────────────────────────────────────────────────────────────────
    w("<header>")
    if _LOGO_DATA_URI:
        w(f'<img src="{_LOGO_DATA_URI}" alt="rwcheck">')
    w(f"<h1>{title}</h1>")
    w('<div class="meta-bar">')
    w(f'<span><b>Dataset version:</b> <code>{_h(meta.get("dataset_version", "n/a"))}</code></span>')
    w(f'<span><b>Rows:</b> {row_count:,}</span>')
    w(f'<span><b>Built:</b> {_h(meta.get("built_at", "n/a"))}</span>')
    w(f'<span><b>Generated:</b> {_h(generated)}</span>')
    w("</div></header>")

    # ── Summary cards ─────────────────────────────────────────────────────────
    w('<div class="summary">')
    for cls, num, lbl in [
        ("total", len(results), "Total references"),
        ("ret", len(retracted), "Retracted"),
        ("clean", len(clean), "Not found in RW"),
        ("unchk", len(unchecked), "Unchecked (no DOI/PMID)"),
    ]:
        w(f'<div class="card {cls}"><div class="num">{num}</div><div class="lbl">{lbl}</div></div>')
    w("</div>")

    # ── Retracted section ─────────────────────────────────────────────────────
    w('<div class="section ret">')
    w(f'<h2>⚠️ Retracted References ({len(retracted)})</h2>')
    if retracted:
        for r in retracted:
            e = r.entry
            year_str = f" ({_h(e.year)})" if e.year else ""
            journal_str = f" — <em>{_h(e.journal)}</em>" if e.journal else ""
            label = f"[{_h(e.key)}] {_h(e.short_author)}{year_str}{journal_str}"
            w('<div class="ret-entry">')
            w("<details open>")
            w(f'<summary>{label} <span class="badge">RETRACTED</span></summary>')
            w('<div class="entry-body">')
            if e.title:
                w(f'<p class="entry-title">{_h(e.title)}</p>')
            w('<table class="kv-table">')
            if e.doi_raw:
                w(f"<tr><td>DOI</td><td>{_doi_link(e.doi_raw)}</td></tr>")
            if e.pmid:
                w(f"<tr><td>PMID</td><td><code>{e.pmid}</code></td></tr>")
            for m in r.all_matches:
                w(f'<tr><td>Nature</td><td>{_h(m.get("retraction_nature"))}</td></tr>')
                w(f'<tr><td>Reason</td><td>{_h(m.get("reason"))}</td></tr>')
                w(f'<tr><td>Retraction date</td><td>{_h(m.get("retraction_date"))}</td></tr>')
                if m.get("retraction_doi_raw"):
                    w(f'<tr><td>Retraction notice</td><td>{_doi_link(m.get("retraction_doi_raw"))}</td></tr>')
                if m.get("retraction_pmid"):
                    w(f'<tr><td>Retraction PMID</td><td><code>{m["retraction_pmid"]}</code></td></tr>')
                w(f'<tr><td>Journal</td><td>{_h(m.get("journal"))}</td></tr>')
                w(f'<tr><td>Paywalled</td><td>{_h(m.get("paywalled"))}</td></tr>')
                break  # show first match in the table
            w("</table>")
            if len(r.all_matches) > 1:
                w(f'<p class="multi-match-note">⚠ This entry matched <b>{len(r.all_matches)}</b> Retraction Watch records.</p>')
            w("</div></details></div>")
    else:
        w('<p class="empty">No retracted references found.</p>')
    w("</div>")

    # ── Clean section ─────────────────────────────────────────────────────────
    w('<div class="section clean">')
    w(f'<h2>✓ Clean References ({len(clean)})</h2>')
    if clean:
        w('<div class="data-table-wrap"><table class="data-table">')
        w("<thead><tr><th>Key</th><th>Authors</th><th>Year</th><th>Journal</th><th>DOI / PMID</th></tr></thead>")
        w("<tbody>")
        for r in clean:
            e = r.entry
            if e.doi_raw:
                id_cell = _doi_link(e.doi_raw)
            elif e.pmid:
                id_cell = f"<code>PMID:{e.pmid}</code>"
            else:
                id_cell = "—"
            w(
                f"<tr>"
                f"<td><code>{_h(e.key)}</code></td>"
                f"<td>{_h(e.short_author)}</td>"
                f"<td>{_h(e.year)}</td>"
                f"<td>{_h(e.journal)}</td>"
                f"<td>{id_cell}</td>"
                f"</tr>"
            )
        w("</tbody></table></div>")
    else:
        w('<p class="empty">No clean references.</p>')
    w("</div>")

    # ── Unchecked section ─────────────────────────────────────────────────────
    w('<div class="section unchk">')
    w(f'<h2>⬜ Unchecked — No DOI or PMID ({len(unchecked)})</h2>')
    if unchecked:
        w('<div class="data-table-wrap"><table class="data-table">')
        w("<thead><tr><th>Key</th><th>Type</th><th>Title</th></tr></thead>")
        w("<tbody>")
        for r in unchecked:
            e = r.entry
            w(
                f"<tr>"
                f"<td><code>{_h(e.key)}</code></td>"
                f"<td>{_h(e.entry_type)}</td>"
                f"<td>{_h(e.title)}</td>"
                f"</tr>"
            )
        w("</tbody></table></div>")
    else:
        w('<p class="empty">All entries had a DOI or PMID.</p>')
    w("</div>")

    # ── Footer ────────────────────────────────────────────────────────────────
    w("<footer>")
    w(
        'Report generated by <a href="https://github.com/your-org/rwcheck">rwcheck</a>. '
        'Data from <a href="https://retractionwatch.com/">Retraction Watch</a>.'
    )
    w("</footer>")
    w("</div></body></html>")

    return "\n".join(parts)


# ── File output ────────────────────────────────────────────────────────────────

def write_reports(
    results: list[BibResult],
    meta: dict[str, str],
    input_path: Path,
    report_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    """Write JSON, Markdown, and HTML reports to disk.

    Parameters
    ----------
    results:
        One :class:`BibResult` per BibTeX entry.
    meta:
        Dataset metadata dict.
    input_path:
        Source ``.bib`` file path (used for stem and default report directory).
    report_dir:
        Directory to write reports into.  Defaults to the directory containing
        *input_path*.

    Returns
    -------
    tuple[Path, Path, Path]
        ``(json_path, md_path, html_path)``
    """
    stem = input_path.stem
    out_dir = Path(report_dir) if report_dir else input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{stem}_rwcheck.json"
    md_path = out_dir / f"{stem}_rwcheck.md"
    html_path = out_dir / f"{stem}_rwcheck.html"

    json_path.write_text(
        generate_json_report(results, meta, input_path), encoding="utf-8"
    )
    md_path.write_text(
        generate_md_report(results, meta, input_path), encoding="utf-8"
    )
    html_path.write_text(
        generate_html_report(results, meta, input_path), encoding="utf-8"
    )

    return json_path, md_path, html_path


# ── Adapter for batch-doi / batch-pmid ────────────────────────────────────────

def batch_results_to_bib_results(batch_results: list[dict[str, Any]]) -> list[BibResult]:
    """Convert :func:`~rwcheck.db.query_batch` output to :class:`BibResult` list.

    Allows ``batch-doi`` and ``batch-pmid`` to reuse the same report generators
    as ``batch-bib`` by wrapping each flat query result in a synthetic
    :class:`~rwcheck.bib.BibEntry`.
    """
    out: list[BibResult] = []
    for item in batch_results:
        query = item["query"]
        matches: list[dict[str, Any]] = item.get("matches", [])
        if item["query_type"] == "doi":
            doi_str = str(query)
            entry = BibEntry(key=doi_str, entry_type="doi", doi=normalize_doi(doi_str), doi_raw=doi_str)
            out.append(BibResult(entry=entry, doi_matches=matches))
        else:
            pmid_val = int(query) if str(query).lstrip("-").isdigit() else None
            entry = BibEntry(key=str(query), entry_type="pmid", pmid=pmid_val)
            out.append(BibResult(entry=entry, pmid_matches=matches))
    return out
