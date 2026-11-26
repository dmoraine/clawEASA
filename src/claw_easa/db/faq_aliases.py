"""Resolve a FAQ ref, current or retired, to entry ids (shared by retrieval)."""
from __future__ import annotations

from claw_easa.db.sqlite import Database


def _ids(db: Database, sql: str, ref: str) -> list[int]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ref,))
            return [row["id"] for row in cur.fetchall()]


def resolve_faq_ref(db: Database, ref: str) -> list[int]:
    """Entry ids for *ref*: current FAQ ``entry_ref`` first, then the alias table."""
    return _ids(
        db,
        "SELECT id FROM regulation_entries WHERE entry_ref = ? AND entry_type = 'FAQ' ORDER BY id",
        ref,
    ) or _ids(
        db, "SELECT entry_id AS id FROM faq_aliases WHERE alias_ref = ? ORDER BY entry_id", ref
    )
