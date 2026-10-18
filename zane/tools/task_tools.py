"""Task tools over the persistent TaskManager."""
from __future__ import annotations

import os
import shutil

from zane.security.permissions import Level
from zane.tasks.reminders import DISCLOSURE
from zane.tasks.task_manager import PRIORITIES, RECURRENCES, TaskError, describe_task
from zane.tools.errors import ToolError, ToolRefusal, user_errors
from zane.tools.memory_tools import export_path
from zane.tools.registry import ApprovalDetails, Tool, ToolContext, ToolOutcome, ToolRegistry, Verification
from zane.tools.schema import I, N, S, obj

_DUE = "Due date: YYYY-MM-DD, YYYY-MM-DDTHH:MM, or tomorrow / friday / in 3 days / tomorrow at 5pm. Use 'none' to clear."


@user_errors(TaskError)
def _add(args, ctx: ToolContext) -> ToolOutcome:
    t = ctx.tasks.add(args["title"], details=args.get("details", ""), project=args.get("project"), priority=args["priority"],
                      due=args.get("due"), recurrence=args.get("recurrence"), estimate_min=args.get("estimate_min"),
                      remind_at=args.get("remind_at"))
    return ToolOutcome(f"Task created: {describe_task(t, ctx.now())}", data={"id": t.id})


def _verify_exists(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    t = ctx.tasks.get(outcome.data["id"])  # type: ignore[index]
    return Verification(t is not None, f"task #{outcome.data['id']} is stored" if t else "task not found after saving")  # type: ignore[index]


@user_errors(TaskError)
def _list(args, ctx: ToolContext) -> ToolOutcome:
    f, kw = args["filter"], {"project": args.get("project"), "limit": args["limit"]}
    tm = ctx.tasks
    tasks = {"pending": lambda: tm.list_tasks(status="pending", **kw), "overdue": lambda: tm.list_tasks(overdue=True, **kw),
             "today": lambda: tm.list_tasks(today=True, **kw), "week": lambda: tm.list_tasks(within_days=7, **kw),
             "done": lambda: tm.list_tasks(status="done", **kw), "all": lambda: tm.list_tasks(status="all", **kw)}[f]()
    if not tasks:
        return ToolOutcome("No tasks match.")
    now = ctx.now()
    return ToolOutcome(f"{len(tasks)} task(s) [{f}]:\n" + "\n".join(describe_task(t, now) for t in tasks), data={"ids": [t.id for t in tasks]})


@user_errors(TaskError)
def _update(args, ctx: ToolContext) -> ToolOutcome:
    fields = {k: args.get(k) for k in ("title", "details", "project", "priority", "due", "recurrence", "estimate_min")}
    t = ctx.tasks.update(args["id"], **fields)
    return ToolOutcome(f"Updated: {describe_task(t, ctx.now())}", data={"id": t.id})


@user_errors(TaskError)
def _complete(args, ctx: ToolContext) -> ToolOutcome:
    tid = args.get("id")
    if tid is None:
        frag = (args.get("match") or "").strip()
        if not frag:
            raise ToolError("Provide a task id or words from its title.")
        hits = ctx.tasks.find_pending(frag)
        if not hits:
            raise ToolError(f"No pending task matches '{frag}'.")
        if len(hits) > 1:
            raise ToolError("More than one task matches; specify an id:\n" + "\n".join(describe_task(t, ctx.now()) for t in hits[:6]))
        tid = hits[0].id
    done, nxt = ctx.tasks.complete(int(tid))
    msg = f"Completed: {describe_task(done, ctx.now())}"
    if nxt:
        msg += f"\nNext occurrence created: {describe_task(nxt, ctx.now())}"
    return ToolOutcome(msg, data={"id": done.id, "next_id": nxt.id if nxt else None})


def _verify_done(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    t = ctx.tasks.get(outcome.data["id"])  # type: ignore[index]
    ok = t is not None and t.status == "done"
    return Verification(ok, f"task #{outcome.data['id']} is marked done" if ok else "task is not marked done")  # type: ignore[index]


def _delete_pre(args, ctx: ToolContext) -> None:
    if ctx.tasks.get(args["id"]) is None:
        raise ToolRefusal(f"No task with id {args['id']}.")


def _delete_describe(args, ctx: ToolContext) -> ApprovalDetails:
    return ApprovalDetails("Delete a task", describe_task(ctx.tasks.get(args["id"]), ctx.now()), "Deleted permanently (completing it keeps it in history instead).")


def _delete(args, ctx: ToolContext) -> ToolOutcome:
    t = ctx.tasks.delete(args["id"])
    return ToolOutcome(f"Deleted task #{t.id}: {t.title}", data={"id": t.id})


@user_errors(TaskError)
def _search(args, ctx: ToolContext) -> ToolOutcome:
    tasks = ctx.tasks.search(args["query"], include_done=args["include_done"])
    if not tasks:
        return ToolOutcome("No tasks match.")
    now = ctx.now()
    return ToolOutcome("\n".join(describe_task(t, now) for t in tasks), data={"ids": [t.id for t in tasks]})


@user_errors(TaskError)
def _remind(args, ctx: ToolContext) -> ToolOutcome:
    t = ctx.tasks.set_reminder(args["id"], args["at"])
    return ToolOutcome(f"Reminder set for {t.remind_at.replace('T', ' ')} on #{t.id}. {DISCLOSURE}", data={"id": t.id})


def _briefing(args, ctx: ToolContext) -> ToolOutcome:
    flags = []
    try:
        u = shutil.disk_usage(os.path.expanduser("~"))
        if u.free < 10e9 or u.free / u.total < 0.10:
            flags.append(f"Low disk space: {u.free / 1e9:.1f} GB free ({u.free / u.total:.0%}).")
    except OSError:
        pass
    return ToolOutcome(ctx.tasks.briefing(flags))


def _plan(args, ctx: ToolContext) -> ToolOutcome:
    return ToolOutcome(ctx.tasks.plan(args["hours"]))


def _export(args, ctx: ToolContext) -> ToolOutcome:
    p = export_path(ctx, "tasks", args["format"])
    n = ctx.tasks.export(p, args["format"])
    return ToolOutcome(f"Exported {n} tasks to {p}.", data={"path": str(p)})


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "task_add", "Create a task with optional priority, deadline, project, recurrence and time estimate.",
        obj({"title": S("Task title", maxLength=200), "details": S("Notes", maxLength=1000),
             "project": S("Project name", maxLength=60), "priority": S("Priority", enum=list(PRIORITIES), default="normal"),
             "due": S(_DUE), "recurrence": S("Repeat", enum=list(RECURRENCES)),
             "estimate_min": I("Estimated minutes", minimum=1, maximum=1440),
             "remind_at": S("Reminder time (same formats as due). Stored in ZANE only.")}, ["title"]),
        _add, Level.SAFE, "tasks", verify=_verify_exists))
    reg.register(Tool(
        "task_list", "List tasks: pending, overdue, due today, due within a week, done, or all.",
        obj({"filter": S("Which tasks", enum=["pending", "overdue", "today", "week", "done", "all"], default="pending"),
             "project": S("Limit to a project", maxLength=60), "limit": I("Max tasks", minimum=1, maximum=100, default=25)}),
        _list, Level.SAFE, "tasks"))
    reg.register(Tool(
        "task_update", "Change a task's title, notes, project, priority, deadline, recurrence or estimate.",
        obj({"id": I("Task id", minimum=1), "title": S("New title", maxLength=200), "details": S("New notes", maxLength=1000),
             "project": S("Project ('none' to clear)", maxLength=60), "priority": S("Priority", enum=list(PRIORITIES)),
             "due": S(_DUE), "recurrence": S("Repeat ('none' to clear)", maxLength=10), "estimate_min": I("Estimated minutes", minimum=1, maximum=1440)},
            ["id"]),
        _update, Level.SAFE, "tasks"))
    reg.register(Tool(
        "task_complete", "Mark a task complete by id, or by words from its title. Recurring tasks spawn their next occurrence.",
        obj({"id": I("Task id", minimum=1), "match": S("Words from the title", maxLength=100)}),
        _complete, Level.SAFE, "tasks", verify=_verify_done))
    reg.register(Tool(
        "task_delete", "Permanently delete a task by id. The user must approve.", obj({"id": I("Task id", minimum=1)}, ["id"]),
        _delete, Level.CONFIRM, "tasks", preflight=_delete_pre, describe=_delete_describe))
    reg.register(Tool(
        "task_search", "Search tasks (pending and completed) by words in the title, notes or project.",
        obj({"query": S("Words", maxLength=100), "include_done": {"type": "boolean", "description": "Include completed tasks", "default": True}}, ["query"]),
        _search, Level.SAFE, "tasks"))
    reg.register(Tool(
        "task_remind", "Set a reminder on a task. Stored in ZANE only and delivered only while ZANE is running.",
        obj({"id": I("Task id", minimum=1), "at": S("Reminder time: ISO, tomorrow at 9am, in 2 hours...")}, ["id", "at"]),
        _remind, Level.SAFE, "tasks", intent="scheduling"))
    reg.register(Tool("task_briefing", "Daily briefing: overdue, due today, next 7 days, heavy days and flags (e.g. low disk space).",
                      obj(), _briefing, Level.SAFE, "tasks"))
    reg.register(Tool("task_plan", "Build a time-boxed plan for the next N hours from pending tasks (overdue first, then today, then priority).",
                      obj({"hours": N("Hours to plan", minimum=0.5, maximum=16, default=6)}), _plan, Level.SAFE, "tasks"))
    reg.register(Tool("task_export", "Export all tasks to a JSON or CSV file in ZANE's export folder.",
                      obj({"format": S("File format", enum=["json", "csv"], default="json")}), _export, Level.SAFE, "tasks"))
