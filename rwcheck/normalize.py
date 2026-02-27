"""DOI and PMID normalisation utilities.

Rules applied to DOIs (in order):
  1. Strip leading/trailing whitespace.
  2. Lowercase.
  3. Remove URL prefixes: ``https://doi.org/``, ``http://doi.org/``,
     ``https://dx.doi.org/``, ``http://dx.doi.org/``.
  4. Remove ``doi:`` prefix (case-insensitive, already lowercased at step 2).
  5. Return ``None`` if the result is empty.
"""

from __future__ import annotations

import re

# Prefixes to strip, in priority order (longest first to avoid partial matches).
_DOI_URL_PREFIXES: tuple[str, ...] = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)

_DOI_PREFIX_RE = re.compile(r"^doi:\s*", re.IGNORECASE)


def normalize_doi(raw: str | None) -> str | None:
    """Return a normalised DOI string, or ``None`` if the input is empty/invalid.

    Parameters
    ----------
    raw:
        Raw DOI string from the dataset or user input.

    Returns
    -------
    str | None
        Lower-cased DOI without any URL prefix, or ``None``.
    """
    if not raw:
        return None

    doi = raw.strip().lower()

    # Strip URL-style prefixes.
    for prefix in _DOI_URL_PREFIXES:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break

    # Strip bare "doi:" prefix.
    doi = _DOI_PREFIX_RE.sub("", doi)

    # Strip any remaining leading/trailing whitespace that survived.
    doi = doi.strip()

    return doi or None


def normalize_pmid(raw: str | int | None) -> int | None:
    """Return a PMID as an integer, or ``None`` if absent / zero / invalid.

    Parameters
    ----------
    raw:
        Raw PMID value (string "0", integer 0, or a valid PubMed ID).

    Returns
    -------
    int | None
    """
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (ValueError, TypeError):
        return None
    return value if value > 0 else None
