"""Lightweight persistent memory. Nothing is stored unless explicitly saved; credentials are refused;
retrieval returns only the few relevant records rather than the whole database."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, NamedTuple, Optional

from zane.memory.database import Database
from zane.security.redaction import contains_secret

KINDS = ("preference", "project", "fact", "decision", "routine", "status", "summary")
CONFIDENCE = ("confirmed", "uncertain")
MAX_CONTENT = 1000
_TS = "%Y-%m-%d %H:%M:%S"
_STOP = set(
    "a an and are as at be but by can do does for from had has have how i if in is it its me my of on or our so that "
    "the their them then there these they this to us was we what when where which who why will with you your about "
    "remember know tell show please zane".split()
)


class MemoryRejected(ValueError):
    pass


class SaveResult(NamedTuple):
    id: int
    action: str  # created | updated | duplicate


@dataclass
class Memory:
    id: int
    kind: str
    key: Optional[str]
    content: str
    project: Optional[str]
    confidence: str
    created_at: str
    updated_at: str
    last_used_at: Optional[str]
    use_count: int


def tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1 and t not in _STOP]


class MemoryService:
    def __init__(self, db: Database, clock: Callable[[], datetime] = datetime.now) -> None:
        self.db = db
        self.clock = clock
        db.migrate(
            "memory",
            [
                [
                    """CREATE TABLE memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, key TEXT, content TEXT NOT NULL,
                        project TEXT, confidence TEXT NOT NULL DEFAULT 'confirmed', created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL, last_used_at TEXT, use_count INTEGER NOT NULL DEFAULT 0)""",
                    "CREATE INDEX idx_memories_kind ON memories(kind, project)",
                    """CREATE TABLE projects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, path TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '', registered_at TEXT NOT NULL)""",
                    "CREATE UNIQUE INDEX idx_projects_name ON projects(lower(name))",
                ]
            ],
        )

    # ---- validation -------------------------------------------------------------------------
    @staticmethod
    def validate(content: str, key: Optional[str] = None) -> str:
        text = " ".join(str(content).split())
        if not text:
            raise MemoryRejected("Nothing to remember.")
        if len(text) > MAX_CONTENT:
            raise MemoryRejected(f"Too long for a memory (max {MAX_CONTENT} characters). Summarize it first.")
        if contains_secret(text) or (key and contains_secret(key)):
            raise MemoryRejected("That looks like a password, key or token. ZANE does not store credentials in memory.")
        return text

    @staticmethod
    def _row(r: Any) -> Memory:
        return Memory(**{k: r[k] for k in r.keys()})

    # ---- core operations --------------------------------------------------------------------
    def save(
        self,
        content: str,
        *,
        kind: str = "fact",
        project: Optional[str] = None,
        key: Optional[str] = None,
        confidence: str = "confirmed",
    ) -> SaveResult:
        if kind not in KINDS:
            raise MemoryRejected(f"kind must be one of: {', '.join(KINDS)}")
        if confidence not in CONFIDENCE:
            raise MemoryRejected(f"confidence must be one of: {', '.join(CONFIDENCE)}")
        text = self.validate(content, key)
        key = " ".join(key.split()) if key else None
        project = project.strip() if project and project.strip() else None
        now = self.clock().strftime(_TS)
        with self.db.transaction():
            if key:
                row = self.db.query_one(
                    "SELECT * FROM memories WHERE kind=? AND lower(key)=lower(?) AND lower(coalesce(project,''))=lower(?)",
                    (kind, key, project or ""),
                )
                if row:
                    if row["content"].lower() == text.lower() and row["confidence"] == confidence:
                        self.db.execute("UPDATE memories SET updated_at=? WHERE id=?", (now, row["id"]))
                        return SaveResult(row["id"], "duplicate")
                    self.db.execute(
                        "UPDATE memories SET content=?, confidence=?, updated_at=? WHERE id=?", (text, confidence, now, row["id"])
                    )
                    return SaveResult(row["id"], "updated")
            dup = self.db.query_one(
                "SELECT id FROM memories WHERE lower(content)=lower(?) AND lower(coalesce(project,''))=lower(?)", (text, project or "")
            )
            if dup:
                self.db.execute("UPDATE memories SET updated_at=? WHERE id=?", (now, dup["id"]))
                return SaveResult(dup["id"], "duplicate")
            res = self.db.execute(
                "INSERT INTO memories(kind, key, content, project, confidence, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (kind, key, text, project, confidence, now, now),
            )
            return SaveResult(int(res.lastrowid or 0), "created")

    def get(self, memory_id: int) -> Optional[Memory]:
        r = self.db.query_one("SELECT * FROM memories WHERE id=?", (int(memory_id),))
        return self._row(r) if r else None

    def update(self, memory_id: int, content: str, confidence: Optional[str] = None) -> Memory:
        cur = self.get(memory_id)
        if cur is None:
            raise MemoryRejected(f"No memory with id {memory_id}.")
        text = self.validate(content, cur.key)
        conf = confidence or cur.confidence
        if conf not in CONFIDENCE:
            raise MemoryRejected(f"confidence must be one of: {', '.join(CONFIDENCE)}")
        self.db.execute(
            "UPDATE memories SET content=?, confidence=?, updated_at=? WHERE id=?",
            (text, conf, self.clock().strftime(_TS), int(memory_id)),
        )
        return self.get(memory_id)  # type: ignore[return-value]

    def delete(self, memory_id: int) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id=?", (int(memory_id),)).rowcount > 0

    def list(self, *, kind: Optional[str] = None, project: Optional[str] = None, limit: int = 50) -> List[Memory]:
        where, params = [], []
        if kind:
            where.append("kind=?"); params.append(kind)
        if project:
            where.append("lower(project)=lower(?)"); params.append(project)
        sql = "SELECT * FROM memories" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC, id DESC LIMIT ?"
        return [self._row(r) for r in self.db.query(sql, params + [int(limit)])]

    def count(self) -> int:
        return int(self.db.query_one("SELECT COUNT(*) AS n FROM memories")["n"])  # type: ignore[index]

    def search(self, query: str, *, project: Optional[str] = None, limit: int = 8) -> List[Memory]:
        qt = tokens(query)
        sql = "SELECT * FROM memories" + (" WHERE lower(project)=lower(?)" if project else "") + " ORDER BY updated_at DESC, id DESC LIMIT 500"
        rows = self.db.query(sql, [project] if project else [])
        if not qt:
            return [self._row(r) for r in rows[:limit]]
        scored = []
        for r in rows:
            meta = f"{r['key'] or ''} {r['project'] or ''}".lower()
            body = r["content"].lower()
            score = sum((2 if t in meta else 0) + (1 if t in body else 0) for t in qt)
            if score:
                scored.append((score, r["updated_at"], r["id"], r))
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return [self._row(x[3]) for x in scored[:limit]]

    def relevant_context(self, text: str, *, project: Optional[str] = None, k: int = 5) -> List[Memory]:
        """The few records worth putting in a prompt. Marks them as used."""
        hits = self.search(text, project=project, limit=k) if tokens(text) else []
        if hits:
            now = self.clock().strftime(_TS)
            for m in hits:
                self.db.execute("UPDATE memories SET last_used_at=?, use_count=use_count+1 WHERE id=?", (now, m.id))
        return hits

    def export_json(self, path: Path) -> int:
        rows = [asdict(m) for m in self.list(limit=100000)]
        data = {"memories": rows, "projects": [dict(p) for p in self.projects()]}
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        return len(rows)

    # ---- project registry (a registered project's folder becomes an approved directory) -------
    def register_project(self, name: str, path: Any, description: str = "") -> None:
        name = " ".join(str(name).split())
        if not 1 <= len(name) <= 60:
            raise MemoryRejected("Project name must be 1-60 characters.")
        now = self.clock().strftime(_TS)
        row = self.db.query_one("SELECT id FROM projects WHERE lower(name)=lower(?)", (name,))
        if row:
            self.db.execute("UPDATE projects SET path=?, description=? WHERE id=?", (str(path), description, row["id"]))
        else:
            self.db.execute(
                "INSERT INTO projects(name, path, description, registered_at) VALUES (?,?,?,?)", (name, str(path), description, now)
            )

    def get_project(self, name: str) -> Optional[Any]:
        return self.db.query_one("SELECT * FROM projects WHERE lower(name)=lower(?)", (" ".join(name.split()),))

    def projects(self) -> List[Any]:
        return self.db.query("SELECT * FROM projects ORDER BY lower(name)")

    def forget_project(self, name: str) -> bool:
        return self.db.execute("DELETE FROM projects WHERE lower(name)=lower(?)", (" ".join(name.split()),)).rowcount > 0

    def project_paths(self) -> List[Path]:
        return [Path(p["path"]) for p in self.projects()]
