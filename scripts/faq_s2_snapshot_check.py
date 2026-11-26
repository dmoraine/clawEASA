#!/usr/bin/env python3
"""Opt-in S2 check of FAQ grouping on an already-migrated COPY of a data dir.

Reads the copy from env ``CLAW_EASA_S2_SNAPSHOT_COPY`` (exit 0, "skipped",
when unset) and refuses the real data dir (``~/.local/share/claw-easa``).
Prints aggregates and entry refs only, never FAQ text.  Exit 1 when a hybrid
top-k holds two results with the same normalised text.

    CLAW_EASA_S2_SNAPSHOT_COPY=/tmp/copy python scripts/faq_s2_snapshot_check.py \\
        [--query Q ...] [--top-k 10] [--compare-raw]

The hybrid queries load the embedding model (``HF_HUB_OFFLINE`` is set) and
use the copy's FAISS index; opening goes through ``retrieval.service`` and so
runs the schema shape sync on the copy.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ENV = "CLAW_EASA_S2_SNAPSHOT_COPY"
DEFAULT_QUERY = "FDP extension commander discretion fatigue"


def _ftl_aggregates(db) -> dict:
    from claw_easa.retrieval.faq_groups import collapse_faq_duplicates, load_entries

    with db.connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT DISTINCT r.faq_entry_id AS id FROM faq_regulation_refs r "
            "JOIN regulation_entries e ON e.id = r.faq_entry_id "
            "WHERE e.entry_type = 'FAQ' AND r.target_ref LIKE 'ORO.FTL%' ORDER BY id"
        ).fetchall()]
        groups = conn.execute(
            "SELECT COUNT(DISTINCT g.group_id) AS n FROM faq_entry_groups g "
            "JOIN faq_regulation_refs r ON r.faq_entry_id = g.entry_id "
            "WHERE r.target_ref LIKE 'ORO.FTL%'"
        ).fetchone()["n"]
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM regulation_entries WHERE entry_type = 'FAQ'"
        ).fetchone()["n"]
    rows = list(load_entries(db, ids).values())
    return {
        "oro_ftl_faq_rows": len(ids),
        "oro_ftl_groups": groups,
        "oro_ftl_visible_after_collapse": len(collapse_faq_duplicates(db, rows)),
        "faq_rows_total": total,
    }


def _top_k_aggregates(rows: list[dict]) -> dict:
    from claw_easa.ingest.faq_identity import normalise

    texts = Counter(normalise(f"{r.get('title') or ''} {r.get('body_text') or ''}") for r in rows)
    return {
        "faq": sum(r.get("entry_type") == "FAQ" for r in rows),
        "non_faq": sum(r.get("entry_type") != "FAQ" for r in rows),
        "duplicate_texts": sum(n - 1 for n in texts.values()),
        "refs": [r.get("entry_ref") for r in rows],
    }


def _print(label: str, values: dict) -> None:
    print(label + "  " + "  ".join(f"{k}: {v}" for k, v in values.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--query", action="append", dest="queries")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--compare-raw", action="store_true",
                        help="also print the ungrouped (factor 1) picture")
    args = parser.parse_args(argv)

    copy = os.environ.get(ENV)
    if not copy:
        print(f"skipped: {ENV} is not set")
        return 0

    from claw_easa.config import Settings, default_data_dir

    data_dir = Path(copy).expanduser().resolve()
    if data_dir == default_data_dir().expanduser().resolve():
        print(f"refusing to run on the real data dir {data_dir}; use a migrated copy",
              file=sys.stderr)
        return 2
    if not data_dir.is_dir():
        print(f"not a directory: {data_dir}", file=sys.stderr)
        return 2

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import claw_easa.config as config
    from claw_easa.retrieval import service
    from claw_easa.retrieval.hybrid import hybrid_search

    config._settings = Settings(data_dir=str(data_dir))
    db = service._open_db()
    failed = False
    try:
        _print("oro.ftl", _ftl_aggregates(db))
        for query in args.queries or [DEFAULT_QUERY]:
            grouped = _top_k_aggregates(service.hybrid(query, top_k=args.top_k))
            failed |= grouped["duplicate_texts"] > 0
            _print(f"hybrid[{query!r}] grouped", grouped)
            if args.compare_raw:
                raw = hybrid_search(db, query, top_k=args.top_k, group_faq=False)
                _print(f"hybrid[{query!r}] raw", _top_k_aggregates(raw))
    finally:
        db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
