from __future__ import annotations

import logging
import re
from pathlib import Path

from claw_easa.db.sqlite import Database

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

INITIAL_VERSION = "001_initial"

#: Shape of the schema this code expects.  ``schema_migrations`` records the
#: bootstrap version; the structural upgrades below detect what a database is
#: missing and are safe to run on every ``init_schema()``.
SCHEMA_VERSION = "002_source_provenance"

log = logging.getLogger(__name__)


def _table_ddl(sql_text: str, table: str) -> str:
    """The body of ``CREATE TABLE IF NOT EXISTS <table> (...)`` in schema.sql."""
    match = re.search(
        rf'CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\);',
        sql_text,
        re.DOTALL,
    )
    if match is None:
        raise RuntimeError(f"schema.sql has no definition for {table}")
    return match.group(1)


class MigrationRunner:
    def __init__(self, db: Database) -> None:
        self.db = db

    def init_schema(self) -> None:
        sql_text = SCHEMA_FILE.read_text()
        self.db.execute_script(sql_text)
        self._record(INITIAL_VERSION)
        self._upgrade_source_documents(sql_text)

    def current_version(self) -> str | None:
        row = self.db.fetch_one(
            "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
        )
        return row["version"] if row else None

    # ── Internals ───────────────────────────────────────────────────────

    def _record(self, version: str) -> None:
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                    (version,),
                )
            conn.commit()

    def _columns(self, table: str) -> list[str]:
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"PRAGMA table_info({table})")
                return [row["name"] for row in cur.fetchall()]

    def _upgrade_source_documents(self, sql_text: str) -> None:
        """Bring a pre-existing source_documents table up to the current shape.

        ``CREATE TABLE IF NOT EXISTS`` leaves an already-created table alone,
        so a database built before revisions were tracked keeps neither the
        ``revision`` column nor the ``incomplete`` status.  Both are needed to
        tell a superseded or unparsed source from a good one.
        """
        row = self.db.fetch_one(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_documents'"
        )
        if row is None:
            return

        existing_sql = row["sql"] or ""
        needs_revision = "revision" not in self._columns("source_documents")
        needs_status = "'incomplete'" not in existing_sql
        if not (needs_revision or needs_status):
            return

        log.info("Migrating source_documents to %s", SCHEMA_VERSION)

        ddl = _table_ddl(sql_text, "source_documents")
        carried = [
            column for column in self._columns("source_documents")
            if re.search(rf'^\s*{re.escape(column)}\s', ddl, re.MULTILINE)
        ]
        columns = ", ".join(carried)

        # SQLite cannot alter a CHECK constraint in place; rebuild the table
        # following the procedure from the ALTER TABLE documentation.  Foreign
        # keys are disabled for the swap so the child rows are not cascaded
        # away with the old table.
        self.db.execute_script(
            "PRAGMA foreign_keys=off;\n"
            "BEGIN;\n"
            f"CREATE TABLE source_documents_migrated (\n{ddl}\n);\n"
            f"INSERT INTO source_documents_migrated ({columns}) "
            f"SELECT {columns} FROM source_documents;\n"
            "DROP TABLE source_documents;\n"
            "ALTER TABLE source_documents_migrated RENAME TO source_documents;\n"
            "COMMIT;\n"
            "PRAGMA foreign_keys=on;\n"
        )
