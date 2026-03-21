from __future__ import annotations

import logging

from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner
from claw_easa.retrieval.exact import lookup_reference, search_references
from claw_easa.retrieval.hybrid import hybrid_search
from claw_easa.retrieval.snippets import search_snippets

log = logging.getLogger(__name__)


def _open_db() -> Database:
    db = Database()
    db.open()
    runner = MigrationRunner(db)
    runner.init_schema()
    return db


def lookup(ref: str) -> list[dict]:
    db = _open_db()
    try:
        return lookup_reference(db, ref)
    finally:
        db.close()


def refs(query: str, limit: int = 20) -> list[dict]:
    db = _open_db()
    try:
        return search_references(db, query, limit=limit)
    finally:
        db.close()


def snippets(query: str, limit: int = 10) -> list[dict]:
    db = _open_db()
    try:
        return search_snippets(db, query, limit=limit)
    finally:
        db.close()


def hybrid(query: str, top_k: int = 15) -> list[dict]:
    db = _open_db()
    try:
        return hybrid_search(db, query, top_k=top_k)
    finally:
        db.close()
