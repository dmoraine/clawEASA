from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from claw_easa.config import Settings, get_settings


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


class _CursorProxy:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def execute(self, sql: str, params: tuple | None = None) -> _CursorProxy:
        self._cursor.execute(sql, params or ())
        return self

    def executemany(self, sql: str, seq: list[tuple]) -> _CursorProxy:
        self._cursor.executemany(sql, seq)
        return self

    def fetchone(self) -> dict | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict]:
        return self._cursor.fetchall()

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def __enter__(self) -> _CursorProxy:
        return self

    def __exit__(self, *exc: Any) -> None:
        self._cursor.close()


class _ConnectionProxy:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def cursor(self) -> _CursorProxy:
        return _CursorProxy(self._conn.cursor())

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def execute(self, sql: str, params: tuple | None = None) -> _CursorProxy:
        return _CursorProxy(self._conn.execute(sql, params or ()))

    def __enter__(self) -> _ConnectionProxy:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self.settings.db_path

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = _dict_factory
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def connection(self) -> Generator[_ConnectionProxy, None, None]:
        if self._conn is None:
            self.open()
        yield _ConnectionProxy(self._conn)

    def fetch_one(self, sql: str, params: tuple | None = None) -> dict | None:
        if self._conn is None:
            self.open()
        cur = self._conn.execute(sql, params or ())
        return cur.fetchone()

    def execute(self, sql: str, params: tuple | None = None) -> None:
        if self._conn is None:
            self.open()
        self._conn.execute(sql, params or ())
        self._conn.commit()

    def execute_script(self, sql: str) -> None:
        if self._conn is None:
            self.open()
        self._conn.executescript(sql)
