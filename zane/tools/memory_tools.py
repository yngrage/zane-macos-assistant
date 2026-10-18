"""Memory and project-registry tools. Saving, changing and forgetting always ask the user first."""
from __future__ import annotations

from pathlib import Path

from zane.memory.memory_service import CONFIDENCE, KINDS, MemoryRejected
from zane.security.permissions import Level
from zane.tools.errors import ToolError, ToolRefusal, user_errors
from zane.tools.paths import PathGuard
from zane.tools.registry import ApprovalDetails, Tool, ToolContext, ToolOutcome, ToolRegistry, Verification
from zane.tools.schema import I, S, obj


def export_path(ctx: ToolContext, stem: str, ext: str) -> Path:
    d = Path(ctx.config.export_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{stem}-{ctx.now().strftime('%Y%m%d-%H%M%S')}.{ext}"


def _line(m) -> str:
    return f"#{m.id} [{m.kind}]{' (' + m.project + ')' if m.project else ''} {m.content}{' (uncertain)' if m.confidence == 'uncertain' else ''}"


# ---- memory ------------------------------------------------------------------------------------
def _save_pre(args, ctx: ToolContext) -> None:
    try:
        ctx.memory.validate(args["content"], args.get("key"))
    except MemoryRejected as exc:
        raise ToolRefusal(str(exc))


def _save_describe(args, ctx: ToolContext) -> ApprovalDetails:
    scope = f" (project: {args['project']})" if args.get("project") else ""
    return ApprovalDetails("Save to ZANE memory", f"[{args['kind']}] {args['content']}{scope}",
                           "Stored locally in ZANE's database. You can list, edit or delete it at any time.")


@user_errors(MemoryRejected)
def _save(args, ctx: ToolContext) -> ToolOutcome:
    r = ctx.memory.save(args["content"], kind=args["kind"], project=args.get("project"), key=args.get("key"),
                        confidence=args["confidence"])
    msg = {"created": f"Saved as memory #{r.id}.", "updated": f"Updated memory #{r.id} (replaced the earlier text).",
           "duplicate": f"Already stored as memory #{r.id}; nothing new added."}[r.action]
    return ToolOutcome(msg, data={"id": r.id, "action": r.action})


def _verify_save(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    m = ctx.memory.get(outcome.data["id"])  # type: ignore[index]
    ok = m is not None and " ".join(args["content"].split()).lower() == m.content.lower()
    return Verification(ok, f"memory #{outcome.data['id']} is in the database" if ok else "record not found after saving")  # type: ignore[index]


def _search(args, ctx: ToolContext) -> ToolOutcome:
    hits = ctx.memory.search(args["query"], project=args.get("project"), limit=args["limit"])
    if not hits:
        return ToolOutcome("No matching memories.")
    return ToolOutcome("\n".join(_line(m) for m in hits), data={"ids": [m.id for m in hits]})


def _forget_pre(args, ctx: ToolContext) -> None:
    if ctx.memory.get(args["id"]) is None:
        raise ToolRefusal(f"No memory with id {args['id']}.")


def _forget_describe(args, ctx: ToolContext) -> ApprovalDetails:
    return ApprovalDetails("Forget a memory", _line(ctx.memory.get(args["id"])), "Deleted permanently from ZANE's database.")


def _forget(args, ctx: ToolContext) -> ToolOutcome:
    if not ctx.memory.delete(args["id"]):
        raise ToolError(f"No memory with id {args['id']}.")
    return ToolOutcome(f"Forgot memory #{args['id']}.", data={"id": args["id"]})


def _update_pre(args, ctx: ToolContext) -> None:
    _forget_pre(args, ctx)
    _save_pre({"content": args["content"], "key": ctx.memory.get(args["id"]).key}, ctx)


def _update_describe(args, ctx: ToolContext) -> ApprovalDetails:
    return ApprovalDetails("Update a memory", f"{_line(ctx.memory.get(args['id']))}  =>  {args['content']}", "The old text is replaced.")


@user_errors(MemoryRejected)
def _update(args, ctx: ToolContext) -> ToolOutcome:
    m = ctx.memory.update(args["id"], args["content"])
    return ToolOutcome(f"Updated memory #{m.id}.", data={"id": m.id})


def _export(args, ctx: ToolContext) -> ToolOutcome:
    p = export_path(ctx, "memory", "json")
    n = ctx.memory.export_json(p)
    return ToolOutcome(f"Exported {n} memories to {p}.", data={"path": str(p)})


# ---- project registry --------------------------------------------------------------------------
def _proj_pre(args, ctx: ToolContext) -> None:
    PathGuard.validate_new_root(args["path"])


def _proj_describe(args, ctx: ToolContext) -> ApprovalDetails:
    real = PathGuard.validate_new_root(args["path"])
    return ApprovalDetails(f"Register project '{args['name']}' and approve its folder", str(real),
                           "ZANE will be able to list and read files under this folder and, with your approval each time, "
                           "change them. Revoke with project_forget or /projects forget.")


@user_errors(MemoryRejected)
def _proj_register(args, ctx: ToolContext) -> ToolOutcome:
    real = PathGuard.validate_new_root(args["path"])
    ctx.memory.register_project(args["name"], real, args.get("description", ""))
    return ToolOutcome(f"Registered project '{args['name']}' at {real}.", data={"name": args["name"], "path": str(real)})


def _verify_proj(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    row = ctx.memory.get_project(args["name"])
    ok = row is not None and row["path"] == outcome.data["path"]  # type: ignore[index]
    return Verification(ok, "project is in the registry" if ok else "project not found after saving")


def _proj_forget_pre(args, ctx: ToolContext) -> None:
    if ctx.memory.get_project(args["name"]) is None:
        raise ToolRefusal(f"No project named '{args['name']}'.")


def _proj_forget_describe(args, ctx: ToolContext) -> ApprovalDetails:
    row = ctx.memory.get_project(args["name"])
    return ApprovalDetails(f"Forget project '{row['name']}' and revoke access", row["path"],
                           "ZANE forgets the saved location and loses access to that folder. No files are touched.")


def _proj_forget(args, ctx: ToolContext) -> ToolOutcome:
    ctx.memory.forget_project(args["name"])
    return ToolOutcome(f"Forgot project '{args['name']}'. ZANE no longer has access to its folder.", data={"name": args["name"]})


def _proj_list(args, ctx: ToolContext) -> ToolOutcome:
    rows = ctx.memory.projects()
    if not rows:
        return ToolOutcome("No projects registered.")
    return ToolOutcome("\n".join(f"{r['name']} - {r['path']}" + (f" ({r['description']})" if r["description"] else "") for r in rows))


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "memory_save", "Save something the user asked you to remember (preference, fact, decision, routine...). Never credentials. The user must approve.",
        obj({"content": S("What to remember, one self-contained sentence", maxLength=1000),
             "kind": S("Category", enum=list(KINDS), default="fact"),
             "project": S("Project this belongs to", maxLength=60),
             "key": S("Optional stable label; saving again with the same key updates the record", maxLength=80),
             "confidence": S("confirmed if the user stated it, uncertain if inferred", enum=list(CONFIDENCE), default="confirmed")},
            ["content"]),
        _save, Level.CONFIRM, "memory", preflight=_save_pre, describe=_save_describe, verify=_verify_save))
    reg.register(Tool(
        "memory_search", "Search saved memories by topic (or list recent ones with an empty query).",
        obj({"query": S("Topic words", maxLength=200), "project": S("Limit to a project", maxLength=60),
             "limit": I("Max results", minimum=1, maximum=25, default=8)}, ["query"]),
        _search, Level.SAFE, "memory"))
    reg.register(Tool(
        "memory_update", "Replace the text of an existing memory by id. The user must approve.",
        obj({"id": I("Memory id", minimum=1), "content": S("New text", maxLength=1000)}, ["id", "content"]),
        _update, Level.CONFIRM, "memory", preflight=_update_pre, describe=_update_describe))
    reg.register(Tool(
        "memory_forget", "Delete a saved memory by id. The user must approve.",
        obj({"id": I("Memory id", minimum=1)}, ["id"]),
        _forget, Level.CONFIRM, "memory", preflight=_forget_pre, describe=_forget_describe))
    reg.register(Tool("memory_export", "Export all memories and projects to a JSON file in ZANE's export folder.", obj(), _export, Level.SAFE, "memory"))
    reg.register(Tool(
        "project_register", "Remember a project's folder and approve it for file access. The user must approve.",
        obj({"name": S("Project name", maxLength=60), "path": S("Absolute folder path"), "description": S("Optional note", maxLength=200)},
            ["name", "path"]),
        _proj_register, Level.CONFIRM, "memory", preflight=_proj_pre, describe=_proj_describe, verify=_verify_proj))
    reg.register(Tool(
        "project_forget", "Forget a saved project location and revoke access to its folder. The user must approve.",
        obj({"name": S("Project name", maxLength=60)}, ["name"]),
        _proj_forget, Level.CONFIRM, "memory", preflight=_proj_forget_pre, describe=_proj_forget_describe))
    reg.register(Tool("project_list", "List registered projects and their folders.", obj(), _proj_list, Level.SAFE, "memory"))
