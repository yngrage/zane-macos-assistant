"""Persistent task manager (SQLite). Times are local and naive; date-only deadlines mean end of that day."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from zane.memory.database import Database
from zane.tasks.dates import add_months, parse_when

PRIORITIES = {"critical": 0, "high": 1, "normal": 2, "low": 3}
PRIORITY_NAMES = {v: k for k, v in PRIORITIES.items()}
RECURRENCES = ("daily", "weekdays", "weekly", "monthly")
DEFAULT_ESTIMATE_MIN = 30
FMT = "%Y-%m-%dT%H:%M"
_EDITABLE = ("title", "details", "project", "priority", "due", "recurrence", "estimate_min")


class TaskError(ValueError):
    pass


@dataclass
class Task:
    id: int
    title: str
    details: str
    project: Optional[str]
    priority: int
    due_at: Optional[str]
    all_day: bool
    status: str
    created_at: str
    completed_at: Optional[str]
    estimate_min: Optional[int]
    recurrence: Optional[str]
    remind_at: Optional[str]
    reminded_at: Optional[str]

    @property
    def priority_name(self) -> str:
        return PRIORITY_NAMES[self.priority]

    @property
    def due(self) -> Optional[datetime]:
        return datetime.strptime(self.due_at, FMT) if self.due_at else None


def fmt_minutes(total: int) -> str:
    h, m = divmod(int(total), 60)
    return f"{h}h{m:02d}m" if h and m else (f"{h}h" if h else f"{m}m")


def describe_task(t: Task, now: datetime) -> str:
    parts = [f"#{t.id} [{t.priority_name.upper()}] {t.title}"]
    d = t.due
    if d:
        label = d.strftime("%a %d %b") + ("" if t.all_day else d.strftime(" %H:%M"))
        rel = ""
        if t.status == "pending":
            if d < now:
                days = (now.date() - d.date()).days
                rel = f" (overdue {days}d)" if days else f" (overdue {int((now - d).total_seconds() // 3600)}h)"
            elif d.date() == now.date():
                rel = " (today)"
            elif d.date() == now.date() + timedelta(days=1):
                rel = " (tomorrow)"
        parts.append(f"due {label}{rel}")
    if t.project:
        parts.append(f"project: {t.project}")
    if t.recurrence:
        parts.append(f"repeats {t.recurrence}")
    if t.estimate_min:
        parts.append(f"~{t.estimate_min}m")
    if t.remind_at:
        parts.append("reminder " + t.remind_at.replace("T", " "))
    if t.status == "done":
        parts.append("done")
    return " | ".join(parts)


class TaskManager:
    def __init__(self, db: Database, clock: Callable[[], datetime] = datetime.now) -> None:
        self.db = db
        self.clock = clock
        db.migrate(
            "tasks",
            [
                [
                    """CREATE TABLE tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
                        project TEXT, priority INTEGER NOT NULL DEFAULT 2, due_at TEXT, all_day INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, completed_at TEXT,
                        estimate_min INTEGER, recurrence TEXT, remind_at TEXT, reminded_at TEXT)""",
                    "CREATE INDEX idx_tasks_status_due ON tasks(status, due_at)",
                ]
            ],
        )

    # ---- helpers ----------------------------------------------------------------------------
    @staticmethod
    def _row(r: Any) -> Task:
        return Task(
            id=r["id"], title=r["title"], details=r["details"], project=r["project"], priority=r["priority"],
            due_at=r["due_at"], all_day=bool(r["all_day"]), status=r["status"], created_at=r["created_at"],
            completed_at=r["completed_at"], estimate_min=r["estimate_min"], recurrence=r["recurrence"],
            remind_at=r["remind_at"], reminded_at=r["reminded_at"],
        )

    @staticmethod
    def _prio(value: Any) -> int:
        v = str(value).strip().lower()
        if v not in PRIORITIES:
            raise TaskError(f"Priority must be one of: {', '.join(PRIORITIES)}.")
        return PRIORITIES[v]

    @staticmethod
    def _rec(value: Any) -> Optional[str]:
        if value is None:
            return None
        v = str(value).strip().lower()
        if v in ("", "none", "no"):
            return None
        if v not in RECURRENCES:
            raise TaskError(f"Recurrence must be one of: {', '.join(RECURRENCES)}.")
        return v

    def _norm_due(self, due: Any) -> Tuple[Optional[str], bool]:
        if due is None:
            return None, False
        if isinstance(due, datetime):
            return due.strftime(FMT), False
        if isinstance(due, date):
            return f"{due.isoformat()}T23:59", True
        s = str(due).strip()
        if not s or s.lower() in ("none", "clear", "no"):
            return None, False
        parsed = parse_when(s, self.clock())
        if not parsed:
            raise TaskError(f"Could not understand the date '{s}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM.")
        dt, all_day = parsed
        return (dt.replace(hour=23, minute=59) if all_day else dt).strftime(FMT), all_day

    def _norm_remind(self, value: Any) -> Optional[str]:
        if value is None or str(value).strip().lower() in ("", "none", "clear"):
            return None
        parsed = parse_when(str(value), self.clock())
        if not parsed:
            raise TaskError(f"Could not understand the reminder time '{value}'.")
        dt, all_day = parsed
        return (dt.replace(hour=9, minute=0) if all_day else dt).strftime(FMT)

    @staticmethod
    def _estimate(value: Any) -> Optional[int]:
        if value is None:
            return None
        v = int(value)
        if not 1 <= v <= 1440:
            raise TaskError("Estimate must be between 1 and 1440 minutes.")
        return v

    # ---- CRUD -------------------------------------------------------------------------------
    def add(
        self,
        title: str,
        *,
        details: str = "",
        project: Optional[str] = None,
        priority: str = "normal",
        due: Any = None,
        recurrence: Optional[str] = None,
        estimate_min: Optional[int] = None,
        remind_at: Any = None,
    ) -> Task:
        title = " ".join(str(title).split())
        if not title:
            raise TaskError("A task needs a title.")
        if len(title) > 200:
            raise TaskError("Title is too long (max 200 characters).")
        prio = self._prio(priority)
        rec = self._rec(recurrence)
        due_at, all_day = self._norm_due(due)
        if rec and not due_at:
            raise TaskError("A recurring task needs a due date.")
        return self._insert(title, details, project, prio, due_at, all_day, self._estimate(estimate_min), rec, self._norm_remind(remind_at))

    def _insert(self, title, details, project, prio, due_at, all_day, estimate, rec, remind) -> Task:
        res = self.db.execute(
            "INSERT INTO tasks(title, details, project, priority, due_at, all_day, status, created_at, estimate_min, recurrence, remind_at) "
            "VALUES (?,?,?,?,?,?, 'pending', ?,?,?,?)",
            (title, details or "", (project or None), prio, due_at, int(all_day), self.clock().strftime(FMT), estimate, rec, remind),
        )
        return self.require(int(res.lastrowid or 0))

    def get(self, task_id: int) -> Optional[Task]:
        r = self.db.query_one("SELECT * FROM tasks WHERE id=?", (int(task_id),))
        return self._row(r) if r else None

    def require(self, task_id: int) -> Task:
        t = self.get(task_id)
        if t is None:
            raise TaskError(f"No task with id {task_id}.")
        return t

    def update(self, task_id: int, **fields: Any) -> Task:
        self.require(task_id)
        sets: List[str] = []
        params: List[Any] = []
        for key, val in fields.items():
            if val is None:
                continue  # None = "not provided"
            if key not in _EDITABLE:
                raise TaskError(f"Cannot change '{key}'.")
            if key == "title":
                title = " ".join(str(val).split())
                if not title or len(title) > 200:
                    raise TaskError("Title must be 1-200 characters.")
                sets.append("title=?"); params.append(title)
            elif key == "priority":
                sets.append("priority=?"); params.append(self._prio(val))
            elif key == "due":
                due_at, all_day = self._norm_due(val)
                sets += ["due_at=?", "all_day=?"]; params += [due_at, int(all_day)]
            elif key == "recurrence":
                sets.append("recurrence=?"); params.append(self._rec(val))
            elif key == "estimate_min":
                sets.append("estimate_min=?"); params.append(self._estimate(val))
            elif key == "project":
                p = str(val).strip()
                sets.append("project=?"); params.append(None if p.lower() in ("", "none") else p)
            else:
                sets.append("details=?"); params.append(str(val))
        if not sets:
            raise TaskError("Nothing to change.")
        self.db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", params + [int(task_id)])
        return self.require(task_id)

    def delete(self, task_id: int) -> Task:
        t = self.require(task_id)
        self.db.execute("DELETE FROM tasks WHERE id=?", (int(task_id),))
        return t

    # ---- completion & recurrence ------------------------------------------------------------
    @staticmethod
    def _advance(dt: datetime, rec: str) -> datetime:
        if rec == "daily":
            return dt + timedelta(days=1)
        if rec == "weekly":
            return dt + timedelta(days=7)
        if rec == "monthly":
            return add_months(dt, 1)
        nxt = dt + timedelta(days=1)  # weekdays
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    def complete(self, task_id: int) -> Tuple[Task, Optional[Task]]:
        t = self.require(task_id)
        if t.status == "done":
            raise TaskError(f"Task #{task_id} is already complete.")
        now = self.clock()
        nxt: Optional[Task] = None
        with self.db.transaction():
            self.db.execute("UPDATE tasks SET status='done', completed_at=? WHERE id=?", (now.strftime(FMT), int(task_id)))
            if t.recurrence and t.due:
                if t.recurrence == "monthly":  # count months from the original date so day 31 survives short months
                    k = 1
                    cand = add_months(t.due, k)
                    while cand <= now and k < 2000:
                        k += 1
                        cand = add_months(t.due, k)
                else:
                    cand = self._advance(t.due, t.recurrence)
                    for _ in range(2000):
                        if cand > now:
                            break
                        cand = self._advance(cand, t.recurrence)
                remind = None
                if t.remind_at:
                    remind = (datetime.strptime(t.remind_at, FMT) + (cand - t.due)).strftime(FMT)
                res = self.db.execute(
                    "INSERT INTO tasks(title, details, project, priority, due_at, all_day, status, created_at, estimate_min, recurrence, remind_at) "
                    "VALUES (?,?,?,?,?,?, 'pending', ?,?,?,?)",
                    (t.title, t.details, t.project, t.priority, cand.strftime(FMT), int(t.all_day), now.strftime(FMT), t.estimate_min, t.recurrence, remind),
                )
                nxt_id = int(res.lastrowid or 0)
            else:
                nxt_id = 0
        if nxt_id:
            nxt = self.require(nxt_id)
        return self.require(task_id), nxt

    # ---- queries ----------------------------------------------------------------------------
    def list_tasks(
        self,
        *,
        status: str = "pending",
        project: Optional[str] = None,
        overdue: bool = False,
        today: bool = False,
        within_days: Optional[int] = None,
        limit: int = 50,
    ) -> List[Task]:
        where: List[str] = []
        params: List[Any] = []
        if status in ("pending", "done"):
            where.append("status=?"); params.append(status)
        if project:
            where.append("lower(project)=lower(?)"); params.append(project)
        now = self.clock()
        if overdue:
            where.append("status='pending' AND due_at IS NOT NULL AND due_at < ?"); params.append(now.strftime(FMT))
        if today:
            where.append("due_at IS NOT NULL AND substr(due_at,1,10)=?"); params.append(now.strftime("%Y-%m-%d"))
        if within_days is not None:
            end = (now + timedelta(days=int(within_days))).strftime("%Y-%m-%d") + "T23:59"
            where.append("due_at IS NOT NULL AND due_at <= ?"); params.append(end)
        order = "completed_at DESC" if status == "done" else "(due_at IS NULL), due_at, priority, id"
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order} LIMIT ?"
        return [self._row(r) for r in self.db.query(sql, params + [int(limit)])]

    def overdue(self) -> List[Task]:
        return self.list_tasks(overdue=True, limit=500)

    def search(self, query: str, *, include_done: bool = True, limit: int = 25) -> List[Task]:
        toks = [t for t in query.lower().split() if t]
        if not toks:
            return []
        where, params = [], []
        for tok in toks:
            where.append("(lower(title) LIKE ? OR lower(details) LIKE ? OR lower(coalesce(project,'')) LIKE ?)")
            params += [f"%{tok}%"] * 3
        if not include_done:
            where.append("status='pending'")
        sql = "SELECT * FROM tasks WHERE " + " AND ".join(where) + " ORDER BY (status='done'), (due_at IS NULL), due_at, id LIMIT ?"
        return [self._row(r) for r in self.db.query(sql, params + [int(limit)])]

    def find_pending(self, fragment: str) -> List[Task]:
        toks = [t for t in fragment.lower().split() if t not in ("the", "a", "an", "task")]
        if not toks:
            return []
        return [t for t in self.list_tasks(status="pending", limit=1000) if all(k in t.title.lower() for k in toks)]

    # ---- reminders (stored in ZANE only) -----------------------------------------------------
    def set_reminder(self, task_id: int, at: Any) -> Task:
        self.require(task_id)
        when = self._norm_remind(at)
        self.db.execute("UPDATE tasks SET remind_at=?, reminded_at=NULL WHERE id=?", (when, int(task_id)))
        return self.require(task_id)

    def due_reminders(self) -> List[Task]:
        rows = self.db.query(
            "SELECT * FROM tasks WHERE status='pending' AND remind_at IS NOT NULL AND reminded_at IS NULL AND remind_at <= ? ORDER BY remind_at",
            (self.clock().strftime(FMT),),
        )
        return [self._row(r) for r in rows]

    def mark_reminded(self, task_id: int) -> None:
        self.db.execute("UPDATE tasks SET reminded_at=? WHERE id=?", (self.clock().strftime(FMT), int(task_id)))

    # ---- export, briefing, planning ---------------------------------------------------------
    def export(self, path: Path, fmt: str = "json") -> int:
        rows = [asdict(t) for t in self.list_tasks(status="all", limit=100000)]
        if fmt == "csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["id", "title"])
            w.writeheader()
            w.writerows(rows)
            Path(path).write_text(buf.getvalue(), encoding="utf-8")
        else:
            Path(path).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return len(rows)

    def briefing(self, extra_flags: Sequence[str] = ()) -> str:
        now = self.clock()
        pending = self.list_tasks(status="pending", limit=1000)
        overdue = [t for t in pending if t.due and t.due < now]
        today = [t for t in pending if t.due and t.due >= now and t.due.date() == now.date()]
        horizon = now.date() + timedelta(days=7)
        upcoming = [t for t in pending if t.due and now.date() < t.due.date() <= horizon]
        hot_undated = [t for t in pending if not t.due and t.priority <= 1]
        lines = [
            f"Briefing - {now.strftime('%A %d %B %Y, %H:%M')}",
            f"Pending {len(pending)} | overdue {len(overdue)} | due today {len(today)} | next 7 days {len(upcoming)}",
        ]

        def block(title: str, items: List[Task], cap: int = 6) -> None:
            if not items:
                return
            lines.append(f"{title} ({len(items)}):")
            lines.extend("  " + describe_task(t, now) for t in items[:cap])
            if len(items) > cap:
                lines.append(f"  ... and {len(items) - cap} more")

        block("Overdue", overdue)
        block("Due today", today)
        block("Next 7 days", upcoming)
        block("High priority, no deadline", hot_undated, cap=4)
        flags: List[str] = []
        by_day: Dict[date, List[Task]] = {}
        for t in pending:
            if t.due and t.due >= now:
                by_day.setdefault(t.due.date(), []).append(t)
        for day, items in sorted(by_day.items()):
            total = sum(t.estimate_min or DEFAULT_ESTIMATE_MIN for t in items)
            if len(items) >= 3 or total > 360:
                flags.append(f"Heavy day {day.strftime('%a %d %b')}: {len(items)} tasks, about {fmt_minutes(total)} of work (missing estimates count as {DEFAULT_ESTIMATE_MIN}m).")
        if overdue:
            flags.append(f"{len(overdue)} task(s) overdue - clear or reschedule them before taking on new work.")
        flags.extend(extra_flags)
        if flags:
            lines.append("Flags:")
            lines.extend("  - " + f for f in flags)
        if not (pending or flags):
            lines.append("Nothing pending. Clear board.")
        return "\n".join(lines)

    def plan(self, hours: float = 6.0) -> str:
        now = self.clock()
        start = now.replace(second=0, microsecond=0)
        start += timedelta(minutes=(-start.minute) % 15)
        budget = int(round(hours * 60))
        pending = self.list_tasks(status="pending", limit=1000)

        def key(t: Task) -> Tuple[int, int, str, int]:
            d = t.due
            bucket = 0 if d and d < now else 1 if d and d.date() == now.date() else 2 if d and (d.date() - now.date()).days <= 7 else 3
            return bucket, t.priority, t.due_at or "9999", t.id

        cursor, remaining = start, budget
        blocks, skipped, defaulted = [], [], 0
        for t in sorted(pending, key=key):
            est = t.estimate_min or DEFAULT_ESTIMATE_MIN
            if est <= remaining:
                blocks.append((cursor, cursor + timedelta(minutes=est), t, t.estimate_min is None))
                cursor += timedelta(minutes=est)
                remaining -= est
                defaulted += 1 if t.estimate_min is None else 0
            else:
                skipped.append(t)
        lines = [f"Plan - next {hours:g}h from {start.strftime('%H:%M')} (overdue first, then today, then by priority)"]
        if not blocks:
            lines.append("No pending tasks fit. Add tasks or estimates." if not pending else "No task fits in that window.")
        for s, e, t, dflt in blocks:
            lines.append(f"  {s.strftime('%H:%M')}-{e.strftime('%H:%M')}  {describe_task(t, now)}{' (default estimate)' if dflt else ''}")
        if skipped:
            lines.append("Did not fit: " + "; ".join(f"#{t.id} {t.title}" for t in skipped[:6]) + (" ..." if len(skipped) > 6 else ""))
        if defaulted:
            lines.append(f"{defaulted} task(s) use the default {DEFAULT_ESTIMATE_MIN}-minute estimate; add estimates for a tighter plan.")
        if remaining >= 15 and blocks:
            lines.append(f"Unallocated: {fmt_minutes(remaining)} (buffer or unplanned work).")
        return "\n".join(lines)
