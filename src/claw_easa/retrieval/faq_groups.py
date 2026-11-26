"""Read-side FAQ grouping (docs/faq-dedup-design.md §4-§5).

The same FAQ text is published in several EASA FAQ domains.  After ranking,
copies sharing a content hash (``faq_groups``/``faq_entry_groups``, built by
migration 005) are shown once: the canonical member is displayed, the other
members are listed in ``also_in``.  A FAQ without a group row — every FAQ of
a database the migration has not touched — is its own group, so an
unmigrated database reads exactly as before.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from claw_easa.config import get_settings
from claw_easa.db.faq_aliases import resolve_faq_ref
from claw_easa.db.faq_migration import VERSION as MIGRATION_VERSION
from claw_easa.db.sqlite import Database

_CHUNK = 500

#: Ranking/match keys carried from the best-scoring member to the displayed one.
SCORE_KEYS = (
    "fts_score", "hybrid_score", "vector_score", "match_source",
    "chunk_text", "chunk_kind", "chunk_index", "matched_subref",
)

# Every column a retrieval path projects for an entry, under the name it uses.
_ENTRY_SELECT = (
    "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, e.body_markdown, "
    "       e.source_url AS entry_url, e.source_locator AS entry_locator, "
    "       d.slug, d.source_url AS document_url, d.page_url AS document_page_url, "
    "       d.revision AS document_revision, p.part_code, sp.subpart_code "
    "FROM regulation_entries e "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
)
#: The keys _ENTRY_SELECT yields: the canonical's identity and provenance.
_ENTRY_KEYS = (
    "id", "entry_ref", "entry_type", "title", "body_text", "body_markdown", "entry_url",
    "entry_locator", "slug", "document_url", "document_page_url", "document_revision",
    "part_code", "subpart_code",
)


def overfetch_factor() -> int:
    return get_settings().faq_overfetch_factor


def format_also_in(row: dict) -> str:
    """Text suffix `` [also in: slugA, slugB]`` for a grouped FAQ row, else ``""``."""
    also = row.get("also_in") or []
    if not also:
        return ""
    return " [also in: " + ", ".join(m["slug"] for m in also) + "]"


def grouping_active(db: Database) -> bool:
    """True once migration 005 is recorded (group rows alone do not count)."""
    try:
        return db.fetch_one(
            "SELECT 1 AS x FROM schema_migrations WHERE version = ?", (MIGRATION_VERSION,)
        ) is not None
    except Exception:  # a database predating schema_migrations
        return False


def _fetch(db: Database, sql: str, ids: Sequence) -> list[dict]:
    """Run *sql* (one ``{ids}`` placeholder list) over *ids* in chunks."""
    out: list[dict] = []
    ids = list(ids)
    with db.connection() as conn:
        with conn.cursor() as cur:
            for start in range(0, len(ids), _CHUNK):
                part = ids[start:start + _CHUNK]
                cur.execute(sql.format(ids=",".join("?" * len(part))), tuple(part))
                out.extend(cur.fetchall())
    return out


def load_entries(db: Database, ids: Iterable[int]) -> dict[int, dict]:
    rows = _fetch(db, _ENTRY_SELECT + "WHERE e.id IN ({ids})", list(dict.fromkeys(ids)))
    return {r["id"]: r for r in rows}


def _group_info(db: Database, entry_ids: Iterable[int]) -> tuple[dict[int, dict], dict]:
    """``({entry_id: group}, {entry_id: [legacy refs]})`` for the grouped ids.

    A group carries content_hash, member_count, canonical_entry_id and its
    members ordered by (domain_size, slug, id); domain_size is computed in SQL.
    """
    ids = [i for i in dict.fromkeys(entry_ids) if i is not None]
    if not ids:
        return {}, {}
    links = _fetch(
        db,
        "SELECT g.entry_id, g.group_id, f.content_hash, f.canonical_entry_id, f.member_count "
        "FROM faq_entry_groups g JOIN faq_groups f ON f.group_id = g.group_id "
        "WHERE g.entry_id IN ({ids})",
        ids,
    )
    if not links:
        return {}, {}
    groups: dict[int, dict] = {}
    for link in links:
        groups.setdefault(link["group_id"], {**link, "members": []})
    members = _fetch(
        db,
        "WITH sizes AS (SELECT document_id, COUNT(*) AS domain_size FROM regulation_entries "
        "               WHERE entry_type = 'FAQ' GROUP BY document_id) "
        "SELECT m.group_id, e.id, e.entry_ref, d.slug "
        "FROM faq_entry_groups m "
        "JOIN regulation_entries e ON e.id = m.entry_id "
        "JOIN source_documents d ON d.id = e.document_id "
        "JOIN sizes s ON s.document_id = e.document_id "
        "WHERE m.group_id IN ({ids}) ORDER BY m.group_id, s.domain_size, d.slug, e.id",
        list(groups),
    )
    for m in members:
        groups[m["group_id"]]["members"].append(m)
    by_entry = {link["entry_id"]: groups[link["group_id"]] for link in links}
    aliases: dict[int, list[str]] = {}
    alias_ids = list(dict.fromkeys(ids + [g["canonical_entry_id"] for g in groups.values()]))
    for a in _fetch(
        db,
        "SELECT entry_id, alias_ref FROM faq_aliases WHERE entry_id IN ({ids}) "
        "ORDER BY entry_id, alias_ref",
        alias_ids,
    ):
        aliases.setdefault(a["entry_id"], []).append(a["alias_ref"])
    return by_entry, aliases


def _annotate(row: dict, group: dict, aliases: dict[int, list[str]]) -> dict:
    canonical = next(
        (m for m in group["members"] if m["id"] == group["canonical_entry_id"]), None,
    )
    row["also_in"] = [
        {"entry_ref": m["entry_ref"], "slug": m["slug"]}
        for m in group["members"] if m["id"] != row["id"]
    ]
    row["content_hash"] = group["content_hash"]
    row["group_size"] = group["member_count"]
    row["canonical_ref"] = canonical["entry_ref"] if canonical else None
    row["is_canonical"] = row["id"] == group["canonical_entry_id"]
    row["legacy_refs"] = aliases.get(row["id"], [])
    return row


def annotate_faq_rows(db: Database, rows: list[dict]) -> list[dict]:
    """Add group keys to grouped FAQ *rows* in place, without collapsing them.

    Identity-preserving: a looked-up copy stays itself.  Rows without a group
    (non-FAQ, unmigrated) are left untouched.
    """
    faq_ids = [r.get("id") for r in rows if r.get("entry_type") == "FAQ"]
    if not faq_ids:
        return rows
    groups, aliases = _group_info(db, faq_ids)
    for row in rows:
        group = groups.get(row.get("id")) if row.get("entry_type") == "FAQ" else None
        if group:
            _annotate(row, group, aliases)
    return rows


def _display_row(best: dict, canonical: dict) -> dict:
    """*canonical*'s identity, text and provenance with *best*'s scores.

    Entry columns the canonical holds are carried even when *best*'s
    projection lacks them (e.g. a vector row), so its provenance is never lost.
    """
    row = dict(best)
    row.update({k: v for k, v in canonical.items() if k in best or k in _ENTRY_KEYS})
    row["id"] = canonical["id"]
    if "entry_id" in best:
        row["entry_id"] = canonical["id"]
    text = best.get("chunk_text")
    if isinstance(text, str) and text.startswith(best["entry_ref"]):
        row["chunk_text"] = canonical["entry_ref"] + text[len(best["entry_ref"]):]
    row.pop("resolved_from_alias", None)
    return row


def _row_id(row: dict):
    return row.get("id") if row.get("id") is not None else row.get("entry_id")


def collapse_faq_duplicates(
    db: Database, rows: Sequence[dict], *, slug: str | None = None,
    score_key: str | None = None,
) -> list[dict]:
    """One row per FAQ content group, in ranking order (see module doc).

    The first row of a group holds its place; the displayed row is the
    canonical member (with *slug*: the best member inside that slug) carrying
    the scores of the best-scoring member.  Non-FAQ and group-less FAQ rows
    pass through as the same objects.
    """
    faq_ids = [_row_id(r) for r in rows if r.get("entry_type") == "FAQ"]
    groups, aliases = _group_info(db, faq_ids) if faq_ids else ({}, {})
    if not groups:
        return list(rows)

    slots: list = []  # a row, or a group key
    members: dict[int, list[dict]] = {}
    for row in rows:
        group = groups.get(_row_id(row)) if row.get("entry_type") == "FAQ" else None
        if group is None:
            slots.append(row)
            continue
        key = group["group_id"]
        if key not in members:
            members[key] = []
            slots.append(key)
        members[key].append(row)

    need = []
    if slug is None:
        for candidates in members.values():
            canonical_id = groups[_row_id(candidates[0])]["canonical_entry_id"]
            if canonical_id not in {_row_id(m) for m in candidates}:
                need.append(canonical_id)
    loaded = load_entries(db, need) if need else {}

    out: list[dict] = []
    for slot in slots:
        if isinstance(slot, dict):
            out.append(slot)
            continue
        candidates = members[slot]
        group = groups[_row_id(candidates[0])]
        best = (
            max(candidates, key=lambda r: r.get(score_key) or 0) if score_key else candidates[0]
        )
        if slug is not None:
            display_src = candidates[0]  # best-ranked inside the requested slug
        else:
            canonical_id = group["canonical_entry_id"]
            display_src = next(
                (m for m in candidates if _row_id(m) == canonical_id), None,
            ) or loaded.get(canonical_id) or candidates[0]
        shown = _display_row(best, {**display_src, "id": _row_id(display_src)})
        out.append(_annotate(shown, group, aliases))
    return out


def faq_rows_for_ref(db: Database, ref: str, *, slug: str | None = None) -> list[dict]:
    """Entry rows a current FAQ ref or a retired alias names (optionally in *slug*)."""
    ids = resolve_faq_ref(db, ref.strip())
    if not ids:
        return []
    found = load_entries(db, ids)
    return [found[i] for i in ids if i in found and (slug is None or found[i]["slug"] == slug)]
