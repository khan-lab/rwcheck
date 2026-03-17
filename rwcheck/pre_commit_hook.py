"""Entry point for the ``rwcheck-pre-commit`` console script.

Called by pre-commit with the list of staged .bib files as arguments.
Exits with code 1 (failing the commit) if any retracted reference is found.

Usage (via pre-commit framework)::

    # .pre-commit-config.yaml
    repos:
      - repo: https://github.com/khan-lab/rwcheck
        rev: v1.1.0
        hooks:
          - id: rwcheck-bib

Manual test::

    rwcheck-pre-commit refs.bib
"""

from __future__ import annotations

import sys
from pathlib import Path

from rwcheck.bib import parse_bib_file
from rwcheck.db import get_connection, query_by_doi, query_by_pmid


def _resolve_db() -> Path:
    """Return the DB path from env or default, with a helpful error if missing."""
    import os

    db = Path(os.environ.get("RW_DB_PATH", str(Path.home() / ".rwcheck" / "rw.sqlite")))
    if not db.exists():
        print(
            f"[rwcheck] Database not found at {db}.\n"
            "Run `rwcheck update` once to build it, or set RW_DB_PATH.",
            file=sys.stderr,
        )
        sys.exit(2)
    return db


def main(argv: list[str] | None = None) -> int:
    """Check all *argv* .bib files; return exit code (0 = clean, 1 = retracted)."""
    files = argv if argv is not None else sys.argv[1:]
    if not files:
        return 0

    db = _resolve_db()
    conn = get_connection(db)

    total = retracted = unchecked = 0
    found_any = False

    for bib_path_str in files:
        bib_path = Path(bib_path_str)
        if not bib_path.exists():
            continue

        entries = parse_bib_file(bib_path)
        for entry in entries:
            total += 1
            if not entry.doi and not entry.pmid:
                unchecked += 1
                continue

            doi_matches = query_by_doi(conn, entry.doi) if entry.doi else []
            pmid_matches = query_by_pmid(conn, entry.pmid) if entry.pmid else []
            seen: set[int] = set()
            all_matches = []
            for m in doi_matches + pmid_matches:
                if m["record_id"] not in seen:
                    seen.add(m["record_id"])
                    all_matches.append(m)

            if all_matches:
                retracted += 1
                if not found_any:
                    print(
                        "\n[rwcheck] Retracted references found — commit blocked.\n"
                        "          Use `rwcheck batch-bib <file>` for a full report.\n",
                        file=sys.stderr,
                    )
                    found_any = True
                m0 = all_matches[0]
                nature = m0.get("retraction_nature") or "Retraction"
                date = (m0.get("retraction_date") or "")[:10]
                reason = (m0.get("reason") or "")[:60]
                print(
                    f"  ✗ [{entry.key}] {nature}"
                    + (f" ({date})" if date else "")
                    + (f" — {reason}" if reason else ""),
                    file=sys.stderr,
                )

    if found_any:
        print(
            f"\n  Summary: {retracted} retracted / {total - unchecked} checked"
            + (f" / {unchecked} without DOI/PMID" if unchecked else ""),
            file=sys.stderr,
        )
        return 1

    return 0


def run() -> None:
    sys.exit(main())
