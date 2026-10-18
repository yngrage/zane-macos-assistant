"""One SQLite file shared by memory, tasks, audit log and settings. Thread-safe via a single RLock."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, NamedTuple, Optional, Sequence


class ExecResult(NamedTuple):
    lastrowid: Optional[int]
    rowcount: int


class Database:
    def __init__(self, path: Any) -> None:
        self.path = str(path)
        memory_db = self.path == ":memory:"
        if not memory_db:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            if not memory_db:
                self._conn.execute("PRAGMA journal_mode=WAL")
        if not memory_db:
            try:
                os.chmod(self.path, 0o600)  # personal data: owner-only
            except OSError:
                pass
        self.migrate("core", [["CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"]])

    def execute(self, sql: str, params: Sequence[Any] = ()) -> ExecResult:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return ExecResult(cur.lastrowid, cur.rowcount)

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def migrate(self, component: str, steps: Sequence[Sequence[str]]) -> None:
        """Apply numbered migration steps once; each step is a list of single SQL statements."""
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_versions (component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT version FROM schema_versions WHERE component=?", (component,)).fetchone()
            current = row["version"] if row else 0
            for idx in range(current, len(steps)):
                with self.transaction():
                    for stmt in steps[idx]:
                        self._conn.execute(stmt)
                    self._conn.execute(
                        "INSERT INTO schema_versions(component, version) VALUES (?, ?) "
                        "ON CONFLICT(component) DO UPDATE SET version=excluded.version",
                        (component, idx + 1),
                    )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
