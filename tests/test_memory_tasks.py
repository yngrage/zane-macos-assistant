import csv
import json
from datetime import datetime

import pytest

from zane.memory.database import Database
from zane.memory.memory_service import MemoryRejected, MemoryService
from zane.tasks.dates import extract_due_suffix, parse_when
from zane.tasks.reminders import DISCLOSURE, ChainNotifier, MacNotifier, ReminderScheduler
from zane.tasks.task_manager import TaskError, TaskManager, describe_task
from helpers import FakeRunner

NOW = datetime(2026, 10, 14, 9, 0)  # Wednesday


@pytest.fixture
def mem(clock):
    return MemoryService(Database(":memory:"), clock=clock)


@pytest.fixture
def tm(clock):
    return TaskManager(Database(":memory:"), clock=clock)


# ---- memory ----------------------------------------------------------------------------------
def test_memory_save_search_update_forget(mem):
    r = mem.save("Main robotics project lives in ~/Projects/robotics", kind="project", key="robotics")
    assert r.action == "created"
    mem.save("I prefer concise answers", kind="preference")
    assert [m.id for m in mem.search("where is my robotics project?")] == [r.id]
    assert mem.search("quantum chromodynamics") == []
    assert mem.update(r.id, "Robotics lives in ~/Code/robotics").content.endswith("~/Code/robotics")
    assert mem.delete(r.id) and mem.get(r.id) is None and not mem.delete(r.id)


def test_memory_dedup_and_key_upsert(mem):
    a = mem.save("Uses dark mode")
    assert mem.save("uses DARK mode").action == "duplicate" and mem.count() == 1
    k1 = mem.save("Project A is in ~/a", kind="project", key="a")
    k2 = mem.save("Project A is now in ~/a2", kind="project", key="a")
    assert (k2.id, k2.action, mem.count()) == (k1.id, "updated", 2)
    assert a.id != k1.id


@pytest.mark.parametrize("secret", ["my api key is sk-ant-abcdefghijklmnop", "password=hunter2", "the token is ghp_abcdefghijklmnopqrstuvwx", ""])
def test_memory_refuses_credentials_and_empty(mem, secret):
    with pytest.raises(MemoryRejected):
        mem.save(secret)
    assert mem.count() == 0


def test_memory_confirmed_vs_uncertain_and_projects(mem):
    mem.save("Deadline is probably Friday", kind="fact", project="robotics", confidence="uncertain")
    mem.save("Uses ROS2", kind="fact", project="robotics")
    mem.save("Unrelated note about tea", kind="fact", project="home")
    hits = mem.search("robotics", project="robotics")
    assert {m.confidence for m in hits} == {"confirmed", "uncertain"} and len(hits) == 2
    with pytest.raises(MemoryRejected):
        mem.save("x", confidence="sure")


def test_memory_relevant_context_is_small_and_marks_use(mem):
    for i in range(10):
        mem.save(f"robotics note number {i}")
    ctx = mem.relevant_context("tell me about robotics", k=3)
    assert len(ctx) == 3 and mem.get(ctx[0].id).use_count == 1
    assert mem.relevant_context("hello", k=3) == []


def test_memory_persists_across_restart_and_exports(tmp_path, clock):
    path = tmp_path / "m.db"
    m1 = MemoryService(Database(path), clock=clock)
    m1.save("Survives restarts", kind="fact")
    m1.register_project("Robotics", tmp_path)
    m1.db.close()
    m2 = MemoryService(Database(path), clock=clock)
    assert [m.content for m in m2.list()] == ["Survives restarts"] and m2.get_project("robotics")["path"] == str(tmp_path)
    assert m2.export_json(tmp_path / "out.json") == 1
    data = json.loads((tmp_path / "out.json").read_text())
    assert data["memories"][0]["content"] == "Survives restarts" and data["projects"][0]["name"] == "Robotics"


def test_project_registry(mem, tmp_path):
    mem.register_project("Robotics", tmp_path / "a")
    mem.register_project("robotics", tmp_path / "b")  # same name, case-insensitive -> upsert
    assert len(mem.projects()) == 1 and mem.project_paths() == [tmp_path / "b"]
    assert mem.forget_project("ROBOTICS") and mem.projects() == [] and not mem.forget_project("robotics")


# ---- dates -----------------------------------------------------------------------------------
@pytest.mark.parametrize("phrase,expected,all_day", [
    ("tomorrow", datetime(2026, 10, 15), True), ("today", datetime(2026, 10, 14), True),
    ("friday", datetime(2026, 10, 16), True), ("monday", datetime(2026, 10, 19), True), ("next wednesday", datetime(2026, 10, 21), True),
    ("in 2 hours", datetime(2026, 10, 14, 11, 0), False), ("in 3 days", datetime(2026, 10, 17), True),
    ("5pm", datetime(2026, 10, 14, 17, 0), False), ("8am", datetime(2026, 10, 15, 8, 0), False),
    ("tomorrow at 9:30am", datetime(2026, 10, 15, 9, 30), False), ("tonight", datetime(2026, 10, 14, 20, 0), False),
    ("2026-12-01", datetime(2026, 12, 1), True), ("2026-12-01T14:30", datetime(2026, 12, 1, 14, 30), False),
])
def test_parse_when(phrase, expected, all_day):
    assert parse_when(phrase, NOW) == (expected, all_day)


@pytest.mark.parametrize("phrase", ["", "gibberish", "5", "2026-13-45", "tomorrow at 25pm", "in banana days"])
def test_parse_when_refuses_ambiguity(phrase):
    assert parse_when(phrase, NOW) is None


def test_extract_due_suffix():
    assert extract_due_suffix("finish the robotics setup tomorrow") == ("finish the robotics setup", "tomorrow")
    assert extract_due_suffix("call mom on friday at 5pm") == ("call mom", "friday at 5pm")
    assert extract_due_suffix("water the plants") == ("water the plants", None)


# ---- tasks -----------------------------------------------------------------------------------
def test_task_priorities_and_ordering(tm):
    tm.add("low one", priority="low", due="2026-10-20")
    tm.add("critical one", priority="critical", due="2026-10-20")
    tm.add("soonest", priority="normal", due="2026-10-15")
    tm.add("undated")
    assert [t.title for t in tm.list_tasks()] == ["soonest", "critical one", "low one", "undated"]
    with pytest.raises(TaskError):
        tm.add("x", priority="urgent")
    with pytest.raises(TaskError):
        tm.add("   ")
    with pytest.raises(TaskError):
        tm.add("x", due="whenever")


def test_task_overdue_follows_the_clock(tm, clock):
    t = tm.add("pay rent", due="2026-10-14")  # all-day: due at end of today
    assert tm.overdue() == []
    clock.advance(days=1)
    assert [x.id for x in tm.overdue()] == [t.id]
    assert "overdue 1d" in describe_task(tm.require(t.id), clock.now)


def test_task_complete_update_delete_search_export(tm, tmp_path):
    t = tm.add("Install ROS", project="robotics", due="tomorrow", estimate_min=45)
    assert tm.update(t.id, priority="high", due="none", title="Install ROS2").due_at is None
    assert [x.id for x in tm.find_pending("install")] == [t.id]
    done, nxt = tm.complete(t.id)
    assert done.status == "done" and nxt is None and tm.list_tasks() == []
    with pytest.raises(TaskError):
        tm.complete(t.id)
    assert [x.id for x in tm.search("ros2")] == [t.id] and tm.search("ros2", include_done=False) == []
    assert tm.export(tmp_path / "t.json", "json") == 1 and json.loads((tmp_path / "t.json").read_text())[0]["title"] == "Install ROS2"
    assert tm.export(tmp_path / "t.csv", "csv") == 1
    assert list(csv.DictReader(open(tmp_path / "t.csv")))[0]["project"] == "robotics"
    assert tm.delete(t.id).id == t.id and tm.get(t.id) is None


@pytest.mark.parametrize("rec,due,expected_next", [
    ("daily", "2026-10-14", "2026-10-15T23:59"), ("weekly", "2026-10-14", "2026-10-21T23:59"),
    ("weekdays", "2026-10-16", "2026-10-19T23:59"), ("monthly", "2026-10-14", "2026-11-14T23:59"),
])
def test_recurring_tasks_spawn_next_occurrence(tm, rec, due, expected_next):
    t = tm.add("repeat me", due=due, recurrence=rec)
    _, nxt = tm.complete(t.id)
    assert nxt.due_at == expected_next and nxt.status == "pending" and nxt.recurrence == rec


def test_recurring_late_completion_skips_past_occurrences(tm, clock):
    t = tm.add("daily thing", due="2026-10-10", recurrence="daily")
    _, nxt = tm.complete(t.id)
    assert nxt.due_at == "2026-10-14T23:59"  # first occurrence that is not already overdue


def test_monthly_recurrence_keeps_day_31_across_short_months(clock):
    clock.now = datetime(2026, 2, 1, 9, 0)
    tm = TaskManager(Database(":memory:"), clock=clock)
    t = tm.add("month end", due="2026-01-31", recurrence="monthly")
    assert tm.complete(t.id)[1].due_at == "2026-02-28T23:59"
    t2 = tm.add("month end 2", due="2026-01-31", recurrence="monthly")
    clock.now = datetime(2026, 3, 1, 9, 0)
    assert tm.complete(t2.id)[1].due_at == "2026-03-31T23:59"


def test_recurrence_needs_a_due_date(tm):
    with pytest.raises(TaskError):
        tm.add("no due", recurrence="daily")


def test_briefing_lists_what_matters_and_flags_problems(tm):
    tm.add("old thing", due="2026-10-12", priority="high")
    tm.add("today thing", due="today at 5pm")
    for i in range(3):
        tm.add(f"friday {i}", due="2026-10-16", estimate_min=120)
    tm.add("someday critical", priority="critical")
    b = tm.briefing(["Low disk space: 4.0 GB free (2%)."])
    assert "overdue 1" in b and "Overdue (1)" in b and "old thing" in b and "Due today (1)" in b
    assert "Heavy day Fri 16 Oct: 3 tasks, about 6h" in b and "High priority, no deadline" in b and "Low disk space" in b
    assert "Nothing pending" in TaskManager(Database(":memory:"), clock=lambda: NOW).briefing()


def test_plan_orders_by_urgency_and_respects_the_window(tm):
    tm.add("low later", priority="low", due="2026-10-20", estimate_min=60)
    tm.add("overdue thing", due="2026-10-12", estimate_min=60)
    tm.add("big task", due="2026-10-15", estimate_min=300)
    tm.add("quick", due="today", estimate_min=30)
    plan = tm.plan(2)
    lines = [l for l in plan.splitlines() if l.startswith("  ")]
    assert "overdue thing" in lines[0] and "quick" in lines[1]
    assert "Did not fit" in plan and "big task" in plan
    assert "09:00-10:00" in lines[0]
    assert "default" not in tm.plan(2).split("Did not fit")[0].replace("Plan - next 2h", "")  # every fitted task had an estimate


def test_tasks_persist_across_restart(tmp_path, clock):
    path = tmp_path / "t.db"
    t1 = TaskManager(Database(path), clock=clock)
    t1.add("survive restart", priority="high", due="tomorrow")
    t1.db.close()
    t2 = TaskManager(Database(path), clock=clock)
    assert [t.title for t in t2.list_tasks()] == ["survive restart"]


# ---- reminders -------------------------------------------------------------------------------
class Sink:
    def __init__(self, ok=True):
        self.ok, self.got = ok, []

    def notify(self, title, message):
        self.got.append((title, message))
        return self.ok


def test_reminders_fire_once_and_only_when_due(tm, clock):
    t = tm.add("call dentist", remind_at="in 2 hours")
    sink = Sink()
    sched = ReminderScheduler(tm, sink)
    assert sched.check_once() == []
    clock.advance(hours=3)
    assert [(x.id, ok) for x, ok in sched.check_once()] == [(t.id, True)]
    assert sched.check_once() == [] and sink.got == [("ZANE reminder", "call dentist")]


def test_undelivered_reminders_are_retried(tm, clock):
    tm.add("retry me", remind_at="in 1 hours")
    clock.advance(hours=2)
    bad = Sink(ok=False)
    sched = ReminderScheduler(tm, bad)
    assert sched.check_once()[0][1] is False
    assert sched.check_once()[0][1] is False and len(bad.got) == 2  # still pending
    sched.notifier = Sink()
    assert sched.check_once()[0][1] is True and sched.check_once() == []


def test_mac_notifier_passes_text_as_argv_not_script_source():
    fr = FakeRunner().when(["osascript"], returncode=0)
    assert MacNotifier(fr).notify("ZANE reminder", 'evil" & do shell script "rm -rf ~')
    argv = fr.calls[0]
    assert argv[-2:] == ['evil" & do shell script "rm -rf ~', "ZANE reminder"]
    assert "evil" not in " ".join(argv[:-2])
    assert ChainNotifier([MacNotifier(FakeRunner()), Sink()]).notify("t", "m")  # falls through to the next notifier


def test_reminder_tool_discloses_its_limits(make_app):
    app = make_app()
    app.tasks.add("x")
    r = app.registry.execute("task_remind", {"id": 1, "at": "tomorrow at 9am"})
    assert r.ok and DISCLOSURE in r.output and "not registered with Apple Reminders" in r.output
