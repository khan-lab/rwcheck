"""rwcheck — Retraction Watch DOI/PMID/BibTeX checker.

Core package providing normalisation, DB queries, and the CLI.
"""

from rwcheck.api import check_batch, check_doi, check_pmid

__version__ = "1.2.0"
__all__ = ["check_doi", "check_pmid", "check_batch"]
