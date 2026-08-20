from __future__ import annotations

import logging
import re
from pathlib import Path

from claw_easa.db.sqlite import Database
from claw_easa.freshness import FRESHNESS_UNKNOWN, USABLE_STATUSES

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

INITIAL_VERSION = "001_initial"

#: Shape of the schema this code expects.  ``schema_migrations`` records the
#: bootstrap version; the structural upgrades below detect what a database is
#: missing and are safe to run on every ``init_schema()``.
SCHEMA_VERSION = "004_corpus_freshness"

#: Tables kept in step with schema.sql, mapped to the SQL that carries a
#: column across a rebuild when its stored values do not survive the new
#: shape.  Columns absent from an older table are not carried at all: they
#: take the default from schema.sql, which is the truthful reading of a
#: database that never recorded them.
#:
#: ``regulation_parts.regulation`` is such a column — parts stored before
#: attribution existed stay unattributed until their source is parsed again,
#: because nothing recorded which regulation stated them.
#:
#: ``corpus_builds.status`` is the one exception.  'qualified' predates
#: freshness grading, and a build recorded then was never compared against
#: what EASA publishes, so it is carried over as 'freshness-unknown' and
#: never as 'current': nothing established that it was up to date.
_MANAGED_TABLES: dict[str, dict[str, str]] = {
    "source_documents": {},
    "regulation_parts": {},
    "corpus_builds": {
        "status": (
            f"CASE status WHEN 'qualified' THEN '{FRESHNESS_UNKNOWN}' "
            f"ELSE status END"
        ),
    },
    "corpus_build_sources": {},
}

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


def _table_shape(ddl: str) -> str:
    """The comparable part of a ``CREATE TABLE`` statement.

    Everything from the opening parenthesis, with whitespace collapsed.  Two
    statements that declare the same columns and constraints compare equal
    however they were laid out, and the table name is left out on purpose:
    SQLite stores the statement as it was written minus ``IF NOT EXISTS``,
    but ``ALTER TABLE ... RENAME`` rewrites it and quotes the name it
    substitutes, so a rebuilt table would otherwise never again compare equal
    to the schema it was rebuilt from — and would be rebuilt on every open.
    """
    return re.sub(r"\s+", " ", ddl[ddl.index("("):]).strip()


class MigrationRunner:
    def __init__(self, db: Database) -> None:
        self.db = db

    def init_schema(self) -> None:
        sql_text = SCHEMA_FILE.read_text()

        # Stale tables are rebuilt *before* the schema script runs: schema.sql
        # indexes columns an older table does not have yet, so creating those
        # indexes first fails on precisely the databases the rebuild exists
        # for.  Dropping a table drops its indexes with it, and everything in
        # schema.sql is CREATE ... IF NOT EXISTS, so the script that follows
        # both creates what is missing and restores what the rebuild took away.
        self._sync_tables(sql_text)
        self.db.execute_script(sql_text)
        self._record(INITIAL_VERSION)
        self._assign_build_roles()

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

    def _stored_ddl(self, table: str) -> str | None:
        row = self.db.fetch_one(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        return row["sql"] if row else None

    def _sync_tables(self, sql_text: str) -> None:
        """Bring every managed table up to the shape schema.sql describes.

        ``CREATE TABLE IF NOT EXISTS`` leaves an already-created table alone,
        so a database built by an earlier version keeps its old columns and
        its old CHECK constraints — a corpus build would still be graded
        against a vocabulary that no longer exists.  SQLite cannot alter a
        CHECK constraint in place, so each stale table is rebuilt.

        A table schema.sql describes but the database does not hold yet is
        left to the schema script, which creates it in the current shape.
        """
        for table, overrides in _MANAGED_TABLES.items():
            stored = self._stored_ddl(table)
            if stored is None:
                continue
            # A table already in the current shape is left untouched: it is
            # compared against the statement _rebuild_table would create, so
            # rebuilding is what a difference means, not how the DDL is laid
            # out or which of the two statements SQLite happens to have kept.
            expected = f"CREATE TABLE {table} (\n{_table_ddl(sql_text, table)}\n)"
            if _table_shape(stored) == _table_shape(expected):
                continue
            log.info("Migrating %s to %s", table, SCHEMA_VERSION)
            self._rebuild_table(sql_text, table, overrides)

    def _rebuild_table(
        self, sql_text: str, table: str, overrides: dict[str, str],
    ) -> None:
        """Recreate *table* from schema.sql, carrying the rows it already holds.

        Follows the table-rebuild procedure from the SQLite ALTER TABLE
        documentation.  Only columns the new shape still defines are carried,
        and foreign keys are disabled for the swap so child rows are not
        cascaded away with the old table.
        """
        ddl = _table_ddl(sql_text, table)
        carried = [
            column for column in self._columns(table)
            if re.search(rf'^\s*{re.escape(column)}\s', ddl, re.MULTILINE)
        ]
        columns = ", ".join(carried)
        selected = ", ".join(overrides.get(column, column) for column in carried)

        self.db.execute_script(
            "PRAGMA foreign_keys=off;\n"
            "BEGIN;\n"
            f"CREATE TABLE {table}_migrated (\n{ddl}\n);\n"
            f"INSERT INTO {table}_migrated ({columns}) "
            f"SELECT {selected} FROM {table};\n"
            f"DROP TABLE {table};\n"
            f"ALTER TABLE {table}_migrated RENAME TO {table};\n"
            "COMMIT;\n"
            "PRAGMA foreign_keys=on;\n"
        )

    def _assign_build_roles(self) -> None:
        """Point the current and rollback slots at a history recorded without them.

        Builds recorded before the slots existed carry no role, yet the head
        of that history *is* the corpus in use — leaving every slot empty
        would report a database that holds a corpus as holding none.  The head
        takes the current slot whatever it is graded, and the most recent
        usable build before it becomes the rollback candidate.

        Only ever runs on a history with no roles at all, so it cannot
        override the slots ``record_build`` maintains.
        """
        if self.db.fetch_one("SELECT 1 FROM corpus_builds WHERE role IS NOT NULL"):
            return

        head = self.db.fetch_one("SELECT id FROM corpus_builds ORDER BY id DESC LIMIT 1")
        if head is None:
            return

        usable = tuple(sorted(USABLE_STATUSES))
        placeholders = ", ".join("?" * len(usable))
        rollback = self.db.fetch_one(
            f"SELECT id FROM corpus_builds WHERE id != ? AND status IN ({placeholders}) "
            f"ORDER BY id DESC LIMIT 1",
            (head["id"], *usable),
        )

        log.info("Assigning corpus build roles at %s", SCHEMA_VERSION)
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE corpus_builds SET role = 'current' WHERE id = ?",
                    (head["id"],),
                )
                if rollback is not None:
                    cur.execute(
                        "UPDATE corpus_builds SET role = 'rollback' WHERE id = ?",
                        (rollback["id"],),
                    )
            conn.commit()
