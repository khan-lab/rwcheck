"""Unit tests for rwcheck.normalize."""

from __future__ import annotations

import pytest

from rwcheck.normalize import normalize_doi, normalize_pmid

# ── normalize_doi ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Already clean
        ("10.1000/xyz123", "10.1000/xyz123"),
        # Uppercase → lowercase
        ("10.1000/XYZ123", "10.1000/xyz123"),
        # Leading/trailing whitespace
        ("  10.1000/xyz123  ", "10.1000/xyz123"),
        # https://doi.org/ prefix
        ("https://doi.org/10.1000/xyz123", "10.1000/xyz123"),
        # http://doi.org/ prefix
        ("http://doi.org/10.1000/xyz123", "10.1000/xyz123"),
        # https://dx.doi.org/ prefix
        ("https://dx.doi.org/10.1000/xyz123", "10.1000/xyz123"),
        # http://dx.doi.org/ prefix
        ("http://dx.doi.org/10.1000/xyz123", "10.1000/xyz123"),
        # doi: prefix
        ("doi:10.1000/xyz123", "10.1000/xyz123"),
        # doi: prefix with space
        ("doi: 10.1000/xyz123", "10.1000/xyz123"),
        # DOI: prefix (uppercase)
        ("DOI:10.1000/xyz123", "10.1000/xyz123"),
        # DOI with slashes in the suffix
        ("https://doi.org/10.1051/e3sconf/202453804025", "10.1051/e3sconf/202453804025"),
        # Empty string → None
        ("", None),
        # Only whitespace → None
        ("   ", None),
        # None input → None
        (None, None),
    ],
)
def test_normalize_doi(raw: str | None, expected: str | None) -> None:
    assert normalize_doi(raw) == expected


# ── normalize_pmid ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Valid integer string
        ("12345678", 12345678),
        # Already int
        (12345678, 12345678),
        # Zero → None
        ("0", None),
        (0, None),
        # Negative → None (treat as absent)
        ("-1", None),
        # Empty string → None
        ("", None),
        # None → None
        (None, None),
        # Non-numeric → None
        ("abc", None),
        # Whitespace → None
        ("   ", None),
        # Valid with whitespace
        ("  99887766  ", 99887766),
    ],
)
def test_normalize_pmid(raw: str | int | None, expected: int | None) -> None:
    assert normalize_pmid(raw) == expected
