"""Pydantic v2 response/request models for the rwcheck REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MetaResponse(BaseModel):
    """Contents of the ``meta`` table — dataset provenance."""

    dataset_version: str = Field(..., description="Short SHA-256 of the source CSV (first 16 hex chars).")
    built_at: str = Field(..., description="UTC ISO-8601 timestamp when the DB was last built.")
    row_count: str = Field(..., description="Number of rows ingested.")
    source_url: str = Field(..., description="URL or file path used to build the DB.")
    csv_sha256: str | None = Field(None, description="Full SHA-256 of the source CSV.")

    model_config = {"extra": "allow"}  # forward-compat with extra meta keys


class RecordSummary(BaseModel):
    """Key fields from a single Retraction Watch record."""

    record_id: int
    title: str | None = None
    journal: str | None = None
    publisher: str | None = None
    author: str | None = None
    country: str | None = None
    retraction_date: str | None = None
    retraction_nature: str | None = None
    reason: str | None = None
    retraction_doi: str | None = Field(None, description="Normalised retraction-notice DOI.")
    retraction_doi_raw: str | None = Field(None, description="Original retraction-notice DOI.")
    original_paper_doi: str | None = Field(None, description="Normalised original paper DOI.")
    original_paper_doi_raw: str | None = Field(None, description="Original paper DOI as in CSV.")
    original_paper_pmid: int | None = None
    retraction_pmid: int | None = None
    paywalled: str | None = None
    urls: str | None = None


class MatchResponse(BaseModel):
    """Response for a single DOI or PMID lookup."""

    query: str = Field(..., description="The original query string.")
    matched: bool = Field(..., description="True if one or more records were found.")
    matches: list[RecordSummary] = Field(default_factory=list)
    meta: MetaResponse


class BatchRequest(BaseModel):
    """Request body for ``POST /check/batch``."""

    dois: list[str] = Field(default_factory=list, description="List of DOIs to check.")
    pmids: list[Any] = Field(
        default_factory=list,
        description="List of PubMed IDs (integers or strings) to check.",
    )

    def total(self) -> int:
        return len(self.dois) + len(self.pmids)


class BatchResult(BaseModel):
    """Single item in a batch response."""

    query: Any = Field(..., description="The original query value (DOI string or PMID int).")
    query_type: str = Field(..., description="'doi' or 'pmid'.")
    matched: bool
    matches: list[RecordSummary] = Field(default_factory=list)


class BatchResponse(BaseModel):
    """Response for ``POST /check/batch``."""

    results: list[BatchResult]
    meta: MetaResponse


class BibCheckEntry(BaseModel):
    """Single entry result from ``POST /check/bib``."""

    key: str
    title: str | None = None
    doi: str | None = None
    pmid: int | None = None
    matched: bool
    matches: list[RecordSummary] = Field(default_factory=list)


class BibCheckResponse(BaseModel):
    """Response for ``POST /check/bib``."""

    total: int
    retracted: int
    clean: int
    unchecked: int
    results: list[BibCheckEntry]
    meta: MetaResponse


class StatsResponse(BaseModel):
    """Response for ``GET /stats`` — aggregate dataset statistics."""

    total_records: int = Field(..., description="Total number of Retraction Watch records.")
    total_journals: int = Field(..., description="Number of distinct journals.")
    total_countries: int = Field(..., description="Number of distinct countries.")
    doi_coverage: int = Field(..., description="Records with an original paper DOI.")
    pmid_coverage: int = Field(..., description="Records with an original paper PMID.")
    by_year: list[list] = Field(..., description="[[year_str, count], ...] sorted ascending.")
    top_journals: list[list] = Field(..., description="[[journal_name, count], ...] top 10 by count.")
    by_country: list[list] = Field(
        default_factory=list,
        description="[[country_name, count], ...] sorted descending by count.",
    )
    meta: MetaResponse
