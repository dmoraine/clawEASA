-- clawEASA SQLite schema
-- Preserves full EASA regulatory hierarchy

PRAGMA foreign_keys = ON;

-- Source document registry
CREATE TABLE IF NOT EXISTS source_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    source_family TEXT NOT NULL CHECK(source_family IN ('ear', 'rulebook', 'faq')),
    title TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    page_url TEXT,
    source_url TEXT,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK(status IN ('registered', 'fetched', 'parsed', 'indexed', 'error')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    parsed_at TEXT,
    indexed_at TEXT
);

-- Source files tracking
CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    file_kind TEXT NOT NULL DEFAULT 'primary',
    checksum TEXT,
    local_path TEXT,
    download_url TEXT,
    downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Regulatory hierarchy
CREATE TABLE IF NOT EXISTS regulation_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    part_code TEXT NOT NULL,
    annex TEXT,
    title TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS regulation_subparts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL REFERENCES regulation_parts(id) ON DELETE CASCADE,
    subpart_code TEXT NOT NULL,
    title TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS regulation_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subpart_id INTEGER NOT NULL REFERENCES regulation_subparts(id) ON DELETE CASCADE,
    section_code TEXT,
    title TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS regulation_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES regulation_parts(id) ON DELETE CASCADE,
    subpart_id INTEGER NOT NULL REFERENCES regulation_subparts(id) ON DELETE CASCADE,
    section_id INTEGER NOT NULL REFERENCES regulation_sections(id) ON DELETE CASCADE,
    entry_ref TEXT NOT NULL,
    entry_type TEXT NOT NULL
        CHECK(entry_type IN ('IR', 'AMC', 'GM', 'CS', 'article', 'appendix', 'FAQ')),
    title TEXT NOT NULL,
    body_markdown TEXT,
    body_text TEXT,
    source_locator TEXT,
    source_url TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_entries_ref_doc
    ON regulation_entries(entry_ref, document_id)
    WHERE entry_type != 'FAQ';

-- Entry chunks for embedding
CREATE TABLE IF NOT EXISTS entry_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES regulation_entries(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_kind TEXT NOT NULL DEFAULT 'whole',
    breadcrumbs_text TEXT,
    chunk_text TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FAISS position mapping
CREATE TABLE IF NOT EXISTS faiss_mapping (
    faiss_position INTEGER PRIMARY KEY,
    chunk_id INTEGER NOT NULL REFERENCES entry_chunks(id) ON DELETE CASCADE
);

-- FAQ cross-references
CREATE TABLE IF NOT EXISTS faq_regulation_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    faq_entry_id INTEGER NOT NULL REFERENCES regulation_entries(id) ON DELETE CASCADE,
    target_ref TEXT NOT NULL
);

-- FTS5 virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    entry_ref,
    title,
    body_text,
    content='regulation_entries',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- FTS triggers for automatic sync
CREATE TRIGGER IF NOT EXISTS entries_fts_insert
    AFTER INSERT ON regulation_entries
BEGIN
    INSERT INTO entries_fts(rowid, entry_ref, title, body_text)
    VALUES (new.id, new.entry_ref, new.title, new.body_text);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_delete
    BEFORE DELETE ON regulation_entries
BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, entry_ref, title, body_text)
    VALUES ('delete', old.id, old.entry_ref, old.title, old.body_text);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_update
    AFTER UPDATE ON regulation_entries
BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, entry_ref, title, body_text)
    VALUES ('delete', old.id, old.entry_ref, old.title, old.body_text);
    INSERT INTO entries_fts(rowid, entry_ref, title, body_text)
    VALUES (new.id, new.entry_ref, new.title, new.body_text);
END;

-- Schema migrations tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
