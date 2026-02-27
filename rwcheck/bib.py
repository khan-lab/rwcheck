"""BibTeX file parser and DOI/PMID extractor.

Pure-Python implementation — no external BibTeX library required.

The parser handles:
  - ``{...}`` and ``"..."`` quoted field values
  - Nested braces in field values (e.g. ``{{Title with {Braces}}``)
  - ``@article``, ``@inproceedings``, ``@book``, etc.
  - ``@comment``, ``@string``, ``@preamble`` blocks (silently skipped)
  - Field aliases: ``journaltitle`` → journal, ``booktitle`` → journal

DOI extraction order (first non-empty wins):
  1. ``doi`` field
  2. ``url`` field (if it contains ``doi.org/`` or ``dx.doi.org/``)

PMID extraction order:
  1. ``pmid`` field
  2. ``eprint`` field when ``eprinttype = {pubmed}``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rwcheck.normalize import normalize_doi, normalize_pmid

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class BibEntry:
    """A single parsed BibTeX entry."""

    key: str
    entry_type: str                       # article, inproceedings, book, …
    title: str | None = None
    authors: str | None = None            # raw ``author`` field
    year: str | None = None
    journal: str | None = None            # journal / booktitle / series
    doi: str | None = None                # normalised DOI
    doi_raw: str | None = None            # original DOI string from .bib
    pmid: int | None = None               # PubMed ID
    raw_fields: dict[str, str] = field(default_factory=dict)

    @property
    def checkable(self) -> bool:
        """True if we have at least a DOI or PMID to look up."""
        return self.doi is not None or self.pmid is not None

    @property
    def short_author(self) -> str:
        """First author surname for display, e.g. 'Smith et al.'"""
        if not self.authors:
            return "Unknown"
        # BibTeX author format: "Last, First and Last2, First2 and …"
        # or "First Last and First2 Last2 and …"
        first = self.authors.split(" and ")[0].strip()
        # If comma-separated: "Last, First" → "Last"
        surname = first.split(",")[0].strip()
        # If no comma, take last word as surname
        if not surname:
            surname = first
        n_authors = len(self.authors.split(" and "))
        return f"{surname} et al." if n_authors > 1 else surname


# ── Parser internals ──────────────────────────────────────────────────────────

# Matches the start of a BibTeX entry: @type{key,  or  @type{key
_ENTRY_START_RE = re.compile(
    r"@\s*(?P<type>\w+)\s*\{\s*(?P<key>[^,\s]+)\s*,",
    re.IGNORECASE,
)
# Special block types to skip entirely.
_SKIP_TYPES = {"comment", "string", "preamble"}


def _scan_braces(text: str, start: int) -> int:
    """Return the index of the matching closing ``}`` for the ``{`` at *start*.

    Parameters
    ----------
    text:
        Full source text.
    start:
        Index of the opening ``{``.

    Returns
    -------
    int
        Index of the matching ``}``.

    Raises
    ------
    ValueError
        If the braces are unbalanced (truncated file).
    """
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"Unbalanced braces starting at position {start}")


def _parse_fields(body: str) -> dict[str, str]:
    """Parse the comma-separated ``field = value`` pairs from an entry body.

    Handles both ``{...}`` and ``"..."`` delimited values, including nested
    braces inside ``{...}`` values.

    Parameters
    ----------
    body:
        The content between the entry key's comma and the final ``}``.

    Returns
    -------
    dict[str, str]
        Lower-cased field names → raw (unquoted) values.
    """
    fields: dict[str, str] = {}
    i = 0
    n = len(body)

    while i < n:
        # Skip whitespace and commas between fields.
        while i < n and body[i] in " \t\n\r,":
            i += 1
        if i >= n:
            break

        # Read field name (alphanumeric + _ + -)
        name_start = i
        while i < n and (body[i].isalnum() or body[i] in "_-"):
            i += 1
        name = body[name_start:i].strip().lower()
        if not name:
            break

        # Skip whitespace and '='
        while i < n and body[i] in " \t\n\r":
            i += 1
        if i >= n or body[i] != "=":
            break
        i += 1  # consume '='

        # Skip whitespace
        while i < n and body[i] in " \t\n\r":
            i += 1
        if i >= n:
            break

        # Read value
        if body[i] == "{":
            close = _scan_braces(body, i)
            value = body[i + 1 : close]
            i = close + 1
        elif body[i] == '"':
            # Double-quoted value (no nesting).
            i += 1
            val_start = i
            while i < n and body[i] != '"':
                i += 1
            value = body[val_start:i]
            if i < n:
                i += 1  # consume closing '"'
        else:
            # Bare value (number or @string macro name — read until comma/}).
            val_start = i
            while i < n and body[i] not in ",}":
                i += 1
            value = body[val_start:i].strip()

        fields[name] = value

    return fields


def _strip_braces(text: str) -> str:
    """Remove surrounding ``{...}`` wrappers (LaTeX emphasis), recursively."""
    text = text.strip()
    while text.startswith("{") and text.endswith("}"):
        text = text[1:-1].strip()
    return text


# ── DOI / PMID extraction ─────────────────────────────────────────────────────

_DOI_IN_URL_RE = re.compile(
    r"https?://(?:dx\.)?doi\.org/(.+)", re.IGNORECASE
)


def _extract_doi(fields: dict[str, str]) -> tuple[str | None, str | None]:
    """Return ``(normalised_doi, raw_doi)`` from a parsed fields dict.

    Tries the ``doi`` field first, then the ``url`` field.
    """
    raw: str | None = None

    doi_field = fields.get("doi", "").strip()
    if doi_field:
        raw = _strip_braces(doi_field)
    else:
        url_field = fields.get("url", "").strip()
        if url_field:
            m = _DOI_IN_URL_RE.search(url_field)
            if m:
                raw = m.group(1).strip()

    if not raw:
        return None, None

    normalised = normalize_doi(raw)
    return normalised, raw


def _extract_pmid(fields: dict[str, str]) -> int | None:
    """Return a PMID from ``pmid`` field or ``eprint``/``eprinttype=pubmed``."""
    pmid_field = fields.get("pmid", "").strip()
    if pmid_field:
        return normalize_pmid(_strip_braces(pmid_field))

    eprint = fields.get("eprint", "").strip()
    eprinttype = fields.get("eprinttype", "").strip().lower()
    if eprint and eprinttype == "pubmed":
        return normalize_pmid(_strip_braces(eprint))

    return None


# ── Public API ────────────────────────────────────────────────────────────────

def parse_bib_file(path: Path) -> list[BibEntry]:
    """Parse a ``.bib`` file and return a list of :class:`BibEntry` objects.

    ``@comment``, ``@string``, and ``@preamble`` blocks are silently skipped.
    Entries that cannot be parsed (e.g. due to truncation) are also skipped
    with a warning printed to stderr.

    Parameters
    ----------
    path:
        Path to the ``.bib`` file.

    Returns
    -------
    list[BibEntry]
    """
    import sys

    text = path.read_text(encoding="utf-8", errors="replace")
    entries: list[BibEntry] = []

    # Find all @type{ starts.
    for m in _ENTRY_START_RE.finditer(text):
        entry_type = m.group("type").lower()

        # Skip special block types.
        if entry_type in _SKIP_TYPES:
            continue

        key = m.group("key").strip()

        # Find the opening brace of the entry body (the '{' in '@type{').
        brace_pos = text.index("{", m.start())
        try:
            close_pos = _scan_braces(text, brace_pos)
        except ValueError:
            print(f"[bib] Warning: unbalanced braces in entry '{key}', skipping.", file=sys.stderr)
            continue

        # Body is everything between the key comma and the closing brace.
        body_start = m.end()  # after '@type{key,'
        body = text[body_start:close_pos]

        fields = _parse_fields(body)

        # Extract display fields.
        title = _strip_braces(fields.get("title", "")) or None
        authors = _strip_braces(fields.get("author", "")) or None
        year = _strip_braces(fields.get("year", "")) or None
        # journal aliases: journal > journaltitle > booktitle > series
        journal = (
            _strip_braces(fields.get("journal", ""))
            or _strip_braces(fields.get("journaltitle", ""))
            or _strip_braces(fields.get("booktitle", ""))
            or _strip_braces(fields.get("series", ""))
            or None
        )

        doi, doi_raw = _extract_doi(fields)
        pmid = _extract_pmid(fields)

        entries.append(
            BibEntry(
                key=key,
                entry_type=entry_type,
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                doi=doi,
                doi_raw=doi_raw,
                pmid=pmid,
                raw_fields=fields,
            )
        )

    return entries
