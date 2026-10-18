"""Append-only record of every tool call: what was asked, whether it was approved, what happened.
Secrets are redacted before storage. Only the user can clear it (slash command), never a tool."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from zane.memory.database import Database
from zane.security.redaction import redact

_MAX = 400


@dataclass
class AuditEntry:
    id: int
    ts: str
    tool: str
    level: int
    category: str
    summary: str
    approval: str  # auto | approved | declined | blocked | refused | n/a
    status: str  # ok | unverified | failed | denied | blocked | invalid | unavailable | cancelled | timeout
    error: str
    duration_ms: int


class AuditLog:
    def __init__(self, db: Database, clock: Callable[[], datetime] = datetime.now) -> None:
        self.db = db
        self.clock = clock
        db.migrate(
            "audit",
            [
                [
                    """CREATE TABLE audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, tool TEXT NOT NULL,
                        level INTEGER NOT NULL, category TEXT NOT NULL, summary TEXT NOT NULL,
                        approval TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                        duration_ms INTEGER NOT NULL DEFAULT 0)""",
                    "CREATE INDEX idx_audit_ts ON audit(ts)",
                ]
            ],
        )

    def record(
        self,
        *,
        tool: str,
        level: int,
        category: str,
        summary: str,
        approval: str,
        status: str,
        error: str = "",
        duration_ms: int = 0,
    ) -> int:
        res = self.db.execute(
            "INSERT INTO audit(ts, tool, level, category, summary, approval, status, error, duration_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                self.clock().strftime("%Y-%m-%d %H:%M:%S"),
                tool,
                int(level),
                category,
                redact(summary)[:_MAX],
                approval,
                status,
                redact(error)[:_MAX],
                int(duration_ms),
            ),
        )
        return int(res.lastrowid or 0)

    def recent(self, limit: int = 20, tool: Optional[str] = None) -> List[AuditEntry]:
        if tool:
            rows = self.db.query("SELECT * FROM audit WHERE tool=? ORDER BY id DESC LIMIT ?", (tool, limit))
        else:
            rows = self.db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return [AuditEntry(**{k: r[k] for k in r.keys()}) for r in rows]

    def count(self) -> int:
        return int(self.db.query_one("SELECT COUNT(*) AS n FROM audit")["n"])  # type: ignore[index]

    def clear(self) -> int:
        return self.db.execute("DELETE FROM audit").rowcount

    def export_json(self, path: Path) -> int:
        rows = [asdict(e) for e in self.recent(limit=100000)]
        Path(path).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return len(rows)
