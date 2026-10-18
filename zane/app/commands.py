"""User-only slash commands. Anything that changes security state (permissions, approved folders, audit log)
lives here, behind the user's own keystrokes - there is deliberately no tool for it that a model could call."""
from __future__ import annotations

import shlex
from typing import Any, Callable

from zane.security.approvals import ApprovalRequest
from zane.security.permissions import CATEGORIES
from zane.tasks.task_manager import describe_task
from zane.tools.paths import PathGuard, PathRefused

HELP = """Commands
  /help                     this list
  /tools                    tools available right now (level, category)
  /tasks    /briefing       pending tasks / daily briefing
  /memory                   list memories      /memory delete ID
  /projects                 list projects      /projects add NAME PATH   /projects forget NAME
  /permissions              show categories    /permissions enable|disable CATEGORY
  /audit [N]                recent actions     /audit clear
  /usage    /state          model usage / what ZANE is doing
  /quit                     leave (Ctrl-D also works). Ctrl-C cancels the running request.
Try: open Safari | how much storage is available | add a high-priority task to finish X tomorrow
     plan my next six hours | give me my daily briefing | remember that I prefer concise answers"""


def _confirm(app: Any, action: str, target: str, impact: str) -> bool:
    return bool(app.approver.request(ApprovalRequest(tool="user-command", level=2, action=action, target=target, consequences=impact)).approved)


def _audit(app: Any, name: str, summary: str, status: str = "ok", approval: str = "approved") -> None:
    app.audit.record(tool=f"user:{name}", level=2, category="admin", summary=summary, approval=approval, status=status)


def handle_command(app: Any, line: str, out: Callable[[str], None]) -> str:
    """Returns 'quit' to leave the REPL, otherwise ''."""
    try:
        parts = shlex.split(line)
    except ValueError as exc:
        out(f"Could not parse that: {exc}")
        return ""
    cmd, rest = parts[0].lower(), parts[1:]
    if cmd in ("/quit", "/exit", "/q"):
        return "quit"
    if cmd == "/help":
        out(HELP)
    elif cmd == "/tools":
        out("\n".join(f"  L{t.level} {t.category:<12} {t.name}" for t in app.registry.available()))
    elif cmd == "/tasks":
        tasks = app.tasks.list_tasks(limit=50)
        now = app.tasks.clock()
        out("\n".join(describe_task(t, now) for t in tasks) if tasks else "No pending tasks.")
    elif cmd == "/briefing":
        out(app.tasks.briefing())
    elif cmd == "/memory":
        if rest[:1] == ["delete"] and len(rest) == 2 and rest[1].isdigit():
            m = app.memory.get(int(rest[1]))
            if m is None:
                out(f"No memory with id {rest[1]}.")
            elif _confirm(app, "Forget a memory", f"#{m.id} {m.content}", "Deleted permanently."):
                app.memory.delete(m.id)
                _audit(app, "memory delete", f"#{m.id}")
                out(f"Forgot memory #{m.id}.")
            else:
                out("Not deleted.")
        else:
            mems = app.memory.list(limit=50)
            out("\n".join(f"#{m.id} [{m.kind}]{' (' + m.project + ')' if m.project else ''} {m.content}" for m in mems) if mems else "No memories saved.")
    elif cmd == "/projects":
        if rest[:1] == ["add"] and len(rest) >= 3:
            name, path = rest[1], " ".join(rest[2:])
            try:
                real = PathGuard.validate_new_root(path)
            except PathRefused as exc:
                out(f"Refused: {exc}")
                return ""
            if _confirm(app, f"Register project '{name}' and approve its folder", str(real),
                        "ZANE may list and read files here and, with approval each time, change them."):
                app.memory.register_project(name, real)
                _audit(app, "projects add", f"{name} -> {real}")
                out(f"Registered '{name}' at {real}.")
            else:
                out("Not registered.")
        elif rest[:1] == ["forget"] and len(rest) == 2:
            row = app.memory.get_project(rest[1])
            if row is None:
                out(f"No project named '{rest[1]}'.")
            elif _confirm(app, f"Forget project '{row['name']}' and revoke access", row["path"], "No files are touched."):
                app.memory.forget_project(rest[1])
                _audit(app, "projects forget", rest[1])
                out("Forgotten; ZANE no longer has access to that folder.")
            else:
                out("Not changed.")
        else:
            rows = app.memory.projects()
            out("\n".join(f"{r['name']} - {r['path']}" for r in rows) if rows else "No projects registered. Add one: /projects add NAME PATH")
    elif cmd == "/permissions":
        if len(rest) == 2 and rest[0] in ("enable", "disable"):
            try:
                (app.policy.enable if rest[0] == "enable" else app.policy.disable)(rest[1])
            except ValueError as exc:
                out(str(exc))
                return ""
            _audit(app, f"permissions {rest[0]}", rest[1])
            out(f"{rest[1]}: {rest[0]}d.")
        else:
            out("\n".join(f"  {c:<13} {'enabled' if app.policy.is_enabled(c) else 'disabled'}" for c in CATEGORIES)
                + "\nLevel 1 runs automatically, Level 2 asks every time, Level 3 is blocked. Change: /permissions enable|disable CATEGORY")
    elif cmd == "/audit":
        if rest[:1] == ["clear"]:
            if _confirm(app, "Clear the audit log", f"{app.audit.count()} entries", "Permanently deletes the record of past actions."):
                n = app.audit.clear()
                _audit(app, "audit clear", f"removed {n} entries")
                out(f"Cleared {n} entries.")
            else:
                out("Not cleared.")
        else:
            n = int(rest[0]) if rest and rest[0].isdigit() else 15
            entries = app.audit.recent(n)
            out("\n".join(f"{e.ts}  L{e.level} {e.tool:<20} {e.status:<11} {e.approval:<9} {e.summary[:70]}" + (f"  !! {e.error[:60]}" if e.error else "")
                          for e in entries) if entries else "Audit log is empty.")
    elif cmd == "/usage":
        out(app.tracker.summary())
    elif cmd == "/state":
        out(f"{app.state.state.value}" + (f" ({app.state.detail})" if app.state.detail else ""))
    else:
        out("Unknown command. /help lists them.")
    return ""
