from __future__ import annotations

from pathlib import Path

from claw_easa.db.sqlite import Database

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


class MigrationRunner:
    def __init__(self, db: Database) -> None:
        self.db = db

    def init_schema(self) -> None:
        sql_text = SCHEMA_FILE.read_text()
        self.db.execute_script(sql_text)
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                    ("001_initial",),
                )
            conn.commit()

    def current_version(self) -> str | None:
        row = self.db.fetch_one(
            "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
        )
        return row["version"] if row else None
