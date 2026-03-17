"""RIS file parser — converts RIS records to :class:`~rwcheck.bib.BibEntry` objects.

Field mapping
-------------

======================  ==========================  =============================
RIS tag(s)              rispy key                   BibEntry field
======================  ==========================  =============================
``TY``                  ``type_of_reference``       ``entry_type``
``TI`` / ``T1``         ``title`` / ``primary_title`` ``title``
``AU``                  ``authors`` (list)          ``authors`` (joined)
``PY`` / ``Y1``         ``year``                    ``year`` (first 4 chars)
``JO`` / ``JF`` / ``T2`` ``journal_name`` / ``secondary_title`` ``journal``
``DO``                  ``doi``                     ``doi`` / ``doi_raw``
``UR``                  ``urls`` (list)             doi / pmid fallback
``AN``                  ``accession_number``        ``pmid``
``ID``                  ``id``                      key (synthesised if absent)
======================  ==========================  =============================
"""

from __future__ import annotations

import re
from pathlib import Path

import rispy

from rwcheck.bib import BibEntry
from rwcheck.normalize import normalize_doi, normalize_pmid

# ── URL pattern matchers ───────────────────────────────────────────────────────

_PUBMED_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.IGNORECASE)
_DOI_URL_RE = re.compile(r"(?:https?://)?(?:dx\.)?doi\.org/(.+)", re.IGNORECASE)

# ── Key sanitiser ─────────────────────────────────────────────────────────────

_NON_WORD = re.compile(r"\W+")


def _make_key(rec: dict, aus: list[str], year: str | None, idx: int) -> str:
    """Synthesise a citation key from the record, falling back to index."""
    key = rec.get("id") or rec.get("label")
    if key:
        return str(key)
    last = aus[0].split(",")[0].strip().lower() if aus else "ref"
    return _NON_WORD.sub("", f"{last}{year or ''}") + f"_{idx + 1}"


# ── Public parser ─────────────────────────────────────────────────────────────


def parse_ris_file(path: Path) -> list[BibEntry]:
    """Parse a ``.ris`` file and return a list of :class:`~rwcheck.bib.BibEntry` objects.

    Uses :mod:`rispy` for parsing, then maps its Pythonic field names back to
    the shared ``BibEntry`` dataclass so all existing report generation code
    works without modification.

    Parameters
    ----------
    path:
        Path to the ``.ris`` file (UTF-8, with ``errors='replace'``).

    Returns
    -------
    list[BibEntry]
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    records: list[dict] = rispy.loads(text)
    entries: list[BibEntry] = []

    for idx, rec in enumerate(records):
        # ── title ─────────────────────────────────────────────────────────────
        title: str | None = rec.get("title") or rec.get("primary_title") or None

        # ── authors ───────────────────────────────────────────────────────────
        aus: list[str] = rec.get("authors") or []
        authors: str | None = " and ".join(aus) if aus else None

        # ── year ──────────────────────────────────────────────────────────────
        raw_year = rec.get("year") or ""
        year: str | None = str(raw_year)[:4] or None

        # ── journal ───────────────────────────────────────────────────────────
        journal: str | None = (
            rec.get("journal_name")
            or rec.get("secondary_title")
            or rec.get("alternate_title1")
            or None
        )

        # ── DOI ───────────────────────────────────────────────────────────────
        doi_raw: str | None = rec.get("doi") or None
        if not doi_raw:
            for url in rec.get("urls") or []:
                m = _DOI_URL_RE.search(url or "")
                if m:
                    doi_raw = m.group(1)
                    break
        doi_norm: str | None = normalize_doi(doi_raw) if doi_raw else None

        # ── PMID ──────────────────────────────────────────────────────────────
        pmid: int | None = None
        an = rec.get("accession_number")
        if an:
            pmid = normalize_pmid(an)
        if pmid is None:
            for url in rec.get("urls") or []:
                m = _PUBMED_URL_RE.search(url or "")
                if m:
                    pmid = normalize_pmid(m.group(1))
                    break

        # ── citation key ──────────────────────────────────────────────────────
        key = _make_key(rec, aus, year, idx)

        entries.append(
            BibEntry(
                key=key,
                entry_type=rec.get("type_of_reference") or "JOUR",
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                doi=doi_norm,
                doi_raw=doi_raw,
                pmid=pmid,
                raw_fields={k: str(v) for k, v in rec.items()},
            )
        )

    return entries
