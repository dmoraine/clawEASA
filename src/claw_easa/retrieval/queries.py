"""SQL query templates for clawEASA retrieval."""

SNIPPET_SEARCH_FTS_SQL = (
    "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
    "       d.slug, p.part_code, sp.subpart_code, "
    "       -fts.rank AS fts_score "
    "FROM entries_fts fts "
    "JOIN regulation_entries e ON e.id = fts.rowid "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
    "WHERE entries_fts MATCH ? "
    "ORDER BY fts.rank "
    "LIMIT ?"
)

SNIPPET_SEARCH_LIKE_SQL = (
    "SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, "
    "       d.slug, p.part_code, sp.subpart_code, "
    "       1.0 AS fts_score "
    "FROM regulation_entries e "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
    "WHERE e.entry_ref LIKE ? OR e.title LIKE ? OR e.body_text LIKE ? "
    "LIMIT ?"
)
