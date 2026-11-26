"""Synthetic FAQ corpora shared by the S1 tests (no EASA text)."""
from __future__ import annotations

from pathlib import Path

from claw_easa.config import Settings, reset_settings
from claw_easa.db.migrations import MigrationRunner
from claw_easa.db.sqlite import Database

NEW_TABLES = ("faq_groups", "faq_entry_groups", "faq_aliases")


def open_db(data_dir: Path, db_file: str = "claw_easa.db") -> Database:
    reset_settings()
    db = Database(settings=Settings(data_dir=str(data_dir), db_file=db_file))
    db.open()
    MigrationRunner(db).init_schema()
    return db


def make_legacy_shaped(db: Database) -> None:
    """Drop the S1 tables: the shape of a database built before them."""
    for table in NEW_TABLES:
        db.execute(f"DROP TABLE IF EXISTS {table}")


def add_domain(db: Database, slug: str, family: str = "faq") -> dict[str, int]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO source_documents (slug, source_family, title) VALUES (?, ?, ?)",
                (slug, family, slug),
            )
            doc = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_parts (document_id, part_code, title) VALUES (?, 'P', 'P')",
                (doc,),
            )
            part = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_subparts (part_id, subpart_code, title) VALUES (?, 'S', 'S')",
                (part,),
            )
            subpart = cur.lastrowid
            cur.execute(
                "INSERT INTO regulation_sections (subpart_id, title) VALUES (?, 'X')", (subpart,)
            )
            section = cur.lastrowid
        conn.commit()
    return {"doc": doc, "part": part, "subpart": subpart, "section": section}


def add_entry(
    db: Database, dom: dict[str, int], ref: str, title: str, body: str,
    entry_type: str = "FAQ", targets: tuple[str, ...] = (),
) -> int:
    """One entry, one chunk prefixed with its ref (as chunking does), one FAISS slot."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO regulation_entries (document_id, part_id, subpart_id, section_id, "
                "entry_ref, entry_type, title, body_markdown, body_text, source_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (dom["doc"], dom["part"], dom["subpart"], dom["section"], ref, entry_type,
                 title, body, body, f"https://example.test/{ref}"),
            )
            entry_id = cur.lastrowid
            cur.execute(
                "INSERT INTO entry_chunks (entry_id, chunk_text) VALUES (?, ?)",
                (entry_id, f"{ref} ({entry_type}) — {title}\n\n{body}"),
            )
            chunk_id = cur.lastrowid
            cur.execute(
                "INSERT INTO faiss_mapping (faiss_position, chunk_id) "
                "VALUES ((SELECT COALESCE(MAX(faiss_position), -1) + 1 FROM faiss_mapping), ?)",
                (chunk_id,),
            )
            for target in targets:
                cur.execute(
                    "INSERT INTO faq_regulation_refs (faq_entry_id, target_ref) VALUES (?, ?)",
                    (entry_id, target),
                )
        conn.commit()
    return entry_id


SHARED = ("Is the shared question answered?", "Yes, the shared answer applies everywhere.")
TIED = ("Is the tied question answered?", "Yes, the tied answer applies in two places.")
DUP = ("Is the repeated question answered?", "Yes, it is repeated within one domain.")
SAME_TITLE = "Can a foreign certificate be used?"
BODIES = (
    "No.",
    "No, not after the transition period ended.",
    "Yes, until the transition period ends.",
    "Only with a validation statement.",
    "Only with a validation statement, see footnote.",
)


def legacy_corpus(db: Database) -> dict[str, int]:
    """Legacy positional refs over five synthetic domains -> {ref: entry id}.

    Sizes: faq-grand 5, faq-parent 3, faq-child 2, faq-alpha 2, faq-beta 2.
    SHARED is in grand/parent/child (canonical: child, the smallest domain);
    TIED is in alpha/beta (same size: alphabetical, alpha); DUP is twice in
    grand (ordinal suffix); five rows share SAME_TITLE with different bodies.
    """
    grand, parent, child = (add_domain(db, s) for s in ("faq-grand", "faq-parent", "faq-child"))
    alpha, beta = add_domain(db, "faq-alpha"), add_domain(db, "faq-beta")
    rows = [
        (grand, "FAQ-grand-001", *SHARED, ("ORO.FTL.110",)),
        (grand, "FAQ-grand-002", *DUP, ()),
        (grand, "FAQ-grand-003", *DUP, ()),
        (grand, "FAQ-grand-004", SAME_TITLE, BODIES[0], ()),
        (grand, "FAQ-grand-005", SAME_TITLE, BODIES[1], ()),
        (parent, "FAQ-parent-001", *SHARED, ("ORO.FTL.110",)),
        (parent, "FAQ-parent-002", SAME_TITLE, BODIES[2], ()),
        (parent, "FAQ-parent-003", "Is the parent-only question answered?", "Only in parent.", ()),
        (child, "FAQ-child-001", *SHARED, ()),
        (child, "FAQ-child-002", SAME_TITLE, BODIES[3], ()),
        (alpha, "FAQ-alpha-001", *TIED, ()),
        (alpha, "FAQ-alpha-002", SAME_TITLE, BODIES[4], ()),
        (beta, "FAQ-beta-001", *TIED, ()),
        (beta, "FAQ-beta-002", "Is the beta-only question answered?", "Only in beta.", ()),
    ]
    ids = {ref: add_entry(db, dom, ref, t, b, targets=refs) for dom, ref, t, b, refs in rows}
    ear = add_domain(db, "some-ear", family="ear")
    add_entry(db, ear, "ORO.FTL.110", "Operator responsibilities", "Rule text.", entry_type="IR")
    return ids


LEGACY_FAQ_COUNT = 14
