-- clawEASA SQLite schema
-- Preserves full EASA regulatory hierarchy

PRAGMA foreign_keys = ON;

-- Source document registry
--
-- 'status' is the ingestion lifecycle of the document — what the pipeline
-- did with it.  'incomplete' means the source was processed but yielded no
-- usable content, so it must not be mistaken for a successful parse.  It is
-- deliberately NOT the operational corpus status: current /
-- freshness-unknown / stale / incomplete / failed are graded per build in
-- corpus_builds, because they depend on what EASA publishes today rather
-- than on what the pipeline did.
--
-- Four unrelated version axes are kept apart on purpose:
--   * the schema shape       -> schema_migrations.version
--   * the corpus build       -> corpus_builds.build_id / tool_version
--   * the manifest shape     -> the manifest's own manifest_version
--   * the EASA edition held  -> revision / published_at below
-- 'revision'             the edition label EASA advertised for the artefact
--                        actually held, e.g. 'March 2026'.
-- 'published_at'         that label normalised to a sortable date, or NULL
--                        when the label cannot be read as one.
-- 'catalog_revision'     what the EASA catalogue advertised the last time it
--                        was read, with 'catalog_checked_at' saying when.
--                        Never assumed: absent means freshness is unknown.
-- 'parser_version'       which parser produced the entries currently held.
CREATE TABLE IF NOT EXISTS source_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    source_family TEXT NOT NULL CHECK(source_family IN ('ear', 'rulebook', 'faq')),
    title TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    page_url TEXT,
    source_url TEXT,
    revision TEXT,
    published_at TEXT,
    catalog_revision TEXT,
    catalog_published_at TEXT,
    catalog_checked_at TEXT,
    parser_version TEXT,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK(status IN ('registered', 'fetched', 'parsed', 'incomplete',
                         'indexed', 'error')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    parsed_at TEXT,
    indexed_at TEXT
);

-- Source files tracking
--
-- 'checksum' holds the SHA-256 of the artefact as downloaded and
-- 'downloaded_at' the moment it was retrieved; the manifest exports them as
-- the source's checksum and retrieved_at.  The newest row is the artefact in
-- use and the one before it is the rollback candidate; nothing queries rule
-- text through the older rows.
CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    file_kind TEXT NOT NULL DEFAULT 'primary',
    checksum TEXT,
    local_path TEXT,
    download_url TEXT,
    downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which regulations a source document is built from.
--
-- One Easy Access Rules document can consolidate more than one regulation:
-- the Information Security EAR publishes both Implementing Regulation
-- (EU) 2023/203 and Delegated Regulation (EU) 2022/1645.  Both number their
-- first annex 'ANNEX I', so the annex label cannot say which regulation
-- states a part — the declaration is recorded here and each part carries the
-- identifier of the regulation stating it.
-- 'part_codes' is the JSON array of part codes the regulation declares.
CREATE TABLE IF NOT EXISTS source_regulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    identifier TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    part_codes TEXT NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(document_id, identifier)
);

-- Regulatory hierarchy
-- 'regulation' is the identifier of the regulation stating the part.  It is
-- NULL when the source declares no regulations, and when no declared
-- regulation claims the part — an unclaimed part is never filed under a
-- regulation by proximity, because that would attribute requirements to a
-- regulation that does not state them.
CREATE TABLE IF NOT EXISTS regulation_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    part_code TEXT NOT NULL,
    annex TEXT,
    title TEXT NOT NULL,
    regulation TEXT,
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
        CHECK(entry_type IN ('IR', 'AMC', 'GM', 'CS', 'INFO', 'article', 'appendix', 'FAQ')),
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

-- Audit workflow storage (canonical JSON + local persistence)
CREATE TABLE IF NOT EXISTS audit_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL UNIQUE,
    report_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    manual_name TEXT NOT NULL,
    manual_version_date TEXT NOT NULL,
    entity_scope TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_path TEXT,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_db_id INTEGER NOT NULL REFERENCES audit_reports(id) ON DELETE CASCADE,
    finding_index INTEGER NOT NULL,
    finding_id TEXT NOT NULL,
    finding_json TEXT NOT NULL,
    UNIQUE(report_db_id, finding_index),
    UNIQUE(report_db_id, finding_id)
);

CREATE TABLE IF NOT EXISTS audit_finding_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT NOT NULL,
    report_db_id INTEGER NOT NULL REFERENCES audit_reports(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL,
    revision_fingerprint TEXT NOT NULL,
    manual_name TEXT NOT NULL,
    manual_section_paragraph TEXT NOT NULL,
    manual_version_date TEXT NOT NULL,
    entity_scope TEXT NOT NULL,
    applicable_easa_references_json TEXT NOT NULL,
    source_hierarchy_notes_json TEXT NOT NULL,
    manual_excerpt TEXT NOT NULL,
    easa_excerpts_json TEXT NOT NULL,
    assessment TEXT NOT NULL,
    compliance_score INTEGER NOT NULL,
    severity TEXT NOT NULL,
    confidence TEXT NOT NULL,
    gap_types_json TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    review_status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(finding_id, revision_number),
    UNIQUE(finding_id, revision_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_audit_finding_revisions_finding_id
    ON audit_finding_revisions(finding_id);

CREATE TABLE IF NOT EXISTS audit_finding_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_db_id INTEGER NOT NULL REFERENCES audit_finding_revisions(id) ON DELETE CASCADE,
    evidence_index INTEGER NOT NULL,
    evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('manual', 'easa')),
    evidence_text TEXT NOT NULL,
    reference_text TEXT,
    UNIQUE(revision_db_id, evidence_kind, evidence_index)
);

-- Corpus build manifest / provenance
--
-- Every build of the corpus is recorded with the provenance of the sources it
-- was built from and an operational status.  The statuses are pipeline
-- labels, never a compliance conclusion:
--   current            every source matches the EASA revision last observed
--   freshness-unknown  the corpus parsed, but no comparable EASA revision was
--                      available to check it against
--   stale              EASA has published something newer than what is held
--   incomplete         a source is missing or contributed nothing
--   failed             the build holds no usable corpus at all
--
-- Builds stay append-only: recording one never updates or deletes an earlier
-- row, so the technical history of what was built when survives intact.  Two
-- of those rows carry a 'role', which is what makes the *operationally*
-- interesting builds explicit without a second table:
--   'current'   the build the active corpus is, whatever its status;
--   'rollback'  the most recent healthy build other than the current one —
--               the last known good corpus to fall back to.
-- 'role' is a slot pointer, 'status' is a grade: a build can hold the current
-- slot while being graded failed, which is precisely when the rollback slot
-- matters.  The partial unique index below allows at most one of each.
--
-- Rows recorded before freshness was tracked carry no comparable EASA
-- revision, so they are migrated to 'freshness-unknown' and never to
-- 'current': nothing established that they were up to date.
CREATE TABLE IF NOT EXISTS corpus_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id TEXT NOT NULL UNIQUE,
    role TEXT CHECK(role IN ('current', 'rollback')),
    status TEXT NOT NULL
        CHECK(status IN ('current', 'freshness-unknown', 'stale',
                         'incomplete', 'failed')),
    tool_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    parser_version TEXT,
    source_count INTEGER NOT NULL DEFAULT 0,
    entry_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_corpus_builds_status
    ON corpus_builds(status, id);

-- At most one build in each slot.  Historical builds hold no role and are
-- not indexed here, so the history can grow without bound while exactly one
-- build is current and at most one is the rollback candidate.
CREATE UNIQUE INDEX IF NOT EXISTS uix_corpus_builds_role
    ON corpus_builds(role) WHERE role IS NOT NULL;

-- Per-source provenance of a recorded build, queryable without parsing the
-- manifest JSON.  Mirrors the exported manifest field for field, so what was
-- carried from the catalogue through ingestion is not dropped on the way in.
--
-- 'status' is the source's ingestion lifecycle, 'freshness' its graded
-- operational status.  They answer different questions and are kept apart:
-- a source can be 'parsed' and still be 'stale'.
CREATE TABLE IF NOT EXISTS corpus_build_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_db_id INTEGER NOT NULL REFERENCES corpus_builds(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    source_family TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,
    freshness TEXT NOT NULL DEFAULT 'freshness-unknown'
        CHECK(freshness IN ('current', 'freshness-unknown', 'stale',
                            'incomplete', 'failed')),
    freshness_basis TEXT,
    freshness_detail TEXT,
    page_url TEXT,
    source_url TEXT,
    revision TEXT,
    published_at TEXT,
    catalog_revision TEXT,
    catalog_published_at TEXT,
    catalog_checked_at TEXT,
    parser_version TEXT,
    checksum TEXT,
    local_path TEXT,
    download_url TEXT,
    retrieved_at TEXT,
    entry_count INTEGER NOT NULL DEFAULT 0,
    parsed_at TEXT,
    UNIQUE(build_db_id, slug)
);

-- Schema migrations tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
