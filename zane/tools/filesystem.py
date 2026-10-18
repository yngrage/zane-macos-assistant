"""File tools. Every path goes through PathGuard; nothing is ever overwritten silently; there is no delete tool."""
from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from zane.security.permissions import Level
from zane.security.redaction import redact
from zane.tools.errors import ToolError, ToolRefusal
from zane.tools.paths import sensitive_reason
from zane.tools.registry import ApprovalDetails, Tool, ToolContext, ToolOutcome, ToolRegistry, Verification
from zane.tools.schema import B, I, S, obj

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
             "build", "dist", "Pods", "DerivedData", ".idea"}
MAX_WRITE_CHARS = 200_000


def _size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


# ---- fs_list -----------------------------------------------------------------------------------
def _list(args, ctx: ToolContext) -> ToolOutcome:
    p = ctx.guard.resolve(args["path"])
    if not p.is_dir():
        raise ToolError(f"{p} is not a directory.")
    try:
        entries = list(os.scandir(p))
    except PermissionError:
        raise ToolError("Permission denied. macOS may need to grant file access to your terminal app "
                        "(System Settings > Privacy & Security > Files and Folders / Full Disk Access).")
    if not args["show_hidden"]:
        entries = [e for e in entries if not e.name.startswith(".")]
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    limit = args["limit"]
    lines: List[str] = []
    for e in entries[:limit]:
        try:
            if e.is_symlink():
                lines.append(f"{e.name}  (symlink)")
            elif e.is_dir(follow_symlinks=False):
                lines.append(f"{e.name}/")
            else:
                st = e.stat(follow_symlinks=False)
                lines.append(f"{e.name}  {_size(st.st_size)}  {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}")
        except OSError:
            lines.append(f"{e.name}  (unreadable)")
    head = f"{p} - {len(entries)} item(s)" + (f", showing first {limit}" if len(entries) > limit else "")
    return ToolOutcome("\n".join([head] + lines), data={"count": len(entries)}, untrusted=True)


# ---- fs_find -----------------------------------------------------------------------------------
def _find(args, ctx: ToolContext) -> ToolOutcome:
    root = ctx.guard.resolve(args["path"])
    if not root.is_dir():
        raise ToolError(f"{root} is not a directory.")
    pattern = (args.get("name") or "").lower()
    ext = (args.get("extension") or "").lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    if not pattern and not ext:
        raise ToolError("Give a name pattern and/or an extension to search for.")
    wild = any(c in pattern for c in "*?[")
    limit, max_depth = args["limit"], args["max_depth"]
    deadline = time.monotonic() + max(ctx.timeout_s - 2, 1)
    found: List[str] = []
    scanned, stopped = 0, ""
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")] if depth < max_depth else []
        for name in filenames + dirnames:
            scanned += 1
            low = name.lower()
            if ext and not low.endswith(ext):
                continue
            if pattern and not (fnmatch.fnmatch(low, pattern) if wild else pattern in low):
                continue
            found.append(str(Path(dirpath, name)))
            if len(found) >= limit:
                stopped = f"stopped at {limit} results"
                break
        if stopped:
            break
        if scanned >= 20000 or time.monotonic() > deadline:
            stopped = "search limit reached"
            break
    head = f"{len(found)} match(es) under {root}" + (f" ({stopped})" if stopped else "")
    return ToolOutcome("\n".join([head] + found), data={"matches": found}, untrusted=True)


# ---- fs_read -----------------------------------------------------------------------------------
def _read_pre(args, ctx: ToolContext) -> None:
    p = ctx.guard.resolve(args["path"])
    if not p.is_file():
        raise ToolRefusal(f"{p} is not a file.")
    why = sensitive_reason(p)
    if why:
        raise ToolRefusal(f"Refusing to read {p.name}: {why}.")


def _read(args, ctx: ToolContext) -> ToolOutcome:
    p = ctx.guard.resolve(args["path"])
    size = p.stat().st_size
    cap = args["max_bytes"]
    try:
        with open(p, "rb") as f:
            if args["tail"] and size > cap:
                f.seek(size - cap)
            data = f.read(cap)
    except PermissionError:
        raise ToolError("Permission denied. macOS may need to grant file access to your terminal app.")
    if b"\x00" in data[:4096]:
        raise ToolError("Binary file; ZANE only reads text.")
    text = data.decode("utf-8", errors="replace")
    safe = redact(text)
    notes = []
    if size > cap:
        notes.append(f"showing the {'last' if args['tail'] else 'first'} {cap} of {size} bytes")
    if safe != text:
        notes.append("secret-like strings were redacted")
    head = f"{p} ({size} bytes{'; ' + '; '.join(notes) if notes else ''})"
    return ToolOutcome(head + "\n" + safe, data={"size": size, "redacted": safe != text}, untrusted=True)


# ---- fs_mkdir ----------------------------------------------------------------------------------
def _mkdir_pre(args, ctx: ToolContext) -> None:
    p = ctx.guard.resolve(args["path"])
    if p.exists():
        raise ToolRefusal(f"{p} already exists.")


def _mkdir(args, ctx: ToolContext) -> ToolOutcome:
    p = ctx.guard.resolve(args["path"])
    try:
        p.mkdir(parents=True)
    except FileExistsError:
        raise ToolError(f"{p} already exists.")
    return ToolOutcome(f"Created folder {p}.", data={"path": str(p)})


# ---- fs_move / fs_rename -------------------------------------------------------------------------
def _move_paths(ctx: ToolContext, src_raw: str, dst_raw: str) -> Tuple[Path, Path]:
    if os.path.islink(os.path.expanduser(src_raw)):
        raise ToolRefusal("Symlinks are not moved; move the real file instead.")
    src, dst = ctx.guard.resolve(src_raw), ctx.guard.resolve(dst_raw)
    if not src.exists():
        raise ToolRefusal(f"{src} does not exist.")
    if ctx.guard.is_root(src):
        raise ToolRefusal("Approved directories themselves cannot be moved.")
    if dst.exists():
        raise ToolRefusal(f"{dst} already exists. ZANE never overwrites existing files.")
    if src.is_dir() and src in dst.parents:
        raise ToolRefusal("A folder cannot be moved into itself.")
    if not dst.parent.is_dir():
        raise ToolRefusal(f"The destination folder {dst.parent} does not exist.")
    for p in (src, dst):
        why = sensitive_reason(p)
        if why:
            raise ToolRefusal(f"Refusing to touch {p.name}: {why}.")
    return src, dst


def _move_pre(args, ctx: ToolContext) -> None:
    _move_paths(ctx, args["source"], args["destination"])


def _move_describe(args, ctx: ToolContext) -> ApprovalDetails:
    src, dst = _move_paths(ctx, args["source"], args["destination"])
    return ApprovalDetails(f"Move {'folder' if src.is_dir() else 'file'}", f"{src} -> {dst}",
                           "Nothing is overwritten. Undo by moving it back.")


def _move(args, ctx: ToolContext) -> ToolOutcome:
    src, dst = _move_paths(ctx, args["source"], args["destination"])
    shutil.move(str(src), str(dst))
    return ToolOutcome(f"Moved {src.name} to {dst}.", data={"src": str(src), "dst": str(dst)})


def _move_verify(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    d = outcome.data or {}
    ok = Path(d["dst"]).exists() and not Path(d["src"]).exists()
    return Verification(ok, f"{d['dst']} exists and the source is gone" if ok else "destination missing or source still present")


def _rename_dest(ctx: ToolContext, args) -> str:
    name = args["new_name"]
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name or len(name.encode()) > 255:
        raise ToolRefusal("new_name must be a plain file name (no slashes).")
    src = ctx.guard.resolve(args["path"])
    return str(src.parent / name)


def _rename_pre(args, ctx: ToolContext) -> None:
    _move_paths(ctx, args["path"], _rename_dest(ctx, args))


def _rename_describe(args, ctx: ToolContext) -> ApprovalDetails:
    src, dst = _move_paths(ctx, args["path"], _rename_dest(ctx, args))
    return ApprovalDetails("Rename", f"{src.name} -> {dst.name} (in {src.parent})", "Nothing is overwritten. Undo by renaming back.")


def _rename(args, ctx: ToolContext) -> ToolOutcome:
    src, dst = _move_paths(ctx, args["path"], _rename_dest(ctx, args))
    shutil.move(str(src), str(dst))
    return ToolOutcome(f"Renamed {src.name} to {dst.name}.", data={"src": str(src), "dst": str(dst)})


# ---- fs_write ----------------------------------------------------------------------------------
def _write_pre(args, ctx: ToolContext) -> None:
    p = ctx.guard.resolve(args["path"])
    why = sensitive_reason(p)
    if why:
        raise ToolRefusal(f"Refusing to write {p.name}: {why}.")
    if p.is_dir():
        raise ToolRefusal(f"{p} is a directory.")
    if not p.parent.is_dir():
        raise ToolRefusal(f"The folder {p.parent} does not exist.")
    if p.exists() and not args["overwrite"]:
        raise ToolRefusal(f"{p} already exists. Pass overwrite=true to replace it (a backup copy is kept).")
    if len(args["content"]) > MAX_WRITE_CHARS:
        raise ToolRefusal(f"Content is larger than {MAX_WRITE_CHARS} characters.")


def _write_describe(args, ctx: ToolContext) -> ApprovalDetails:
    p = ctx.guard.resolve(args["path"])
    n = len(args["content"].encode("utf-8"))
    if p.exists():
        return ApprovalDetails("OVERWRITE existing file", str(p),
                               f"Replaces {p.stat().st_size} bytes with {n} bytes. A backup copy of the old file ({p.name}.zane-bak-<timestamp>) is kept next to it.")
    return ApprovalDetails("Create new file", str(p), f"Writes {n} bytes. Nothing existing is touched.")


def _write(args, ctx: ToolContext) -> ToolOutcome:
    p = ctx.guard.resolve(args["path"])
    content: str = args["content"]
    backup = ""
    if p.exists():
        bak = p.with_name(f"{p.name}.zane-bak-{ctx.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(p, bak)
        backup = str(bak)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".zane-tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            shutil.copymode(p, tmp)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    else:
        try:
            with open(p, "x", encoding="utf-8", newline="") as f:  # exclusive create: never clobbers a racing file
                f.write(content)
        except FileExistsError:
            raise ToolError(f"{p} appeared while writing; nothing was overwritten.")
    msg = f"Wrote {len(content.encode('utf-8'))} bytes to {p}." + (f" Backup: {backup}" if backup else "")
    return ToolOutcome(msg, data={"path": str(p), "backup": backup, "bytes": len(content.encode("utf-8"))})


def _write_verify(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    p = Path(outcome.data["path"])  # type: ignore[index]
    ok = p.is_file() and p.read_bytes() == args["content"].encode("utf-8")
    return Verification(ok, "file content matches what was written" if ok else "file content differs from what was written")


def register(reg: ToolRegistry) -> None:
    path = S("Absolute path (or ~/...) inside an approved directory")
    reg.register(Tool(
        "fs_list", "List the contents of a directory inside an approved directory.",
        obj({"path": path, "show_hidden": B("Include dotfiles", default=False),
             "limit": I("Max entries", minimum=1, maximum=500, default=100)}, ["path"]),
        _list, Level.SAFE, "filesystem"))
    reg.register(Tool(
        "fs_find", "Find files or folders by name pattern and/or extension under an approved directory (bounded search).",
        obj({"path": path, "name": S("Substring or glob, e.g. report or *.csv", maxLength=100),
             "extension": S("Extension such as .py or py", maxLength=12),
             "limit": I("Max results", minimum=1, maximum=200, default=50),
             "max_depth": I("Max folder depth", minimum=1, maximum=10, default=6)}, ["path"]),
        _find, Level.SAFE, "filesystem", timeout_s=20))
    reg.register(Tool(
        "fs_read", "Read a text file (first or last N bytes) from an approved directory. Credential-looking files are refused and secrets are redacted.",
        obj({"path": path, "max_bytes": I("Bytes to read", minimum=1000, maximum=100000, default=20000),
             "tail": B("Read the end of the file (useful for logs)", default=False)}, ["path"]),
        _read, Level.SAFE, "filesystem", preflight=_read_pre))
    reg.register(Tool(
        "fs_mkdir", "Create a new folder inside an approved directory. Fails if it already exists.",
        obj({"path": path}, ["path"]), _mkdir, Level.SAFE, "filesystem", preflight=_mkdir_pre,
        verify=lambda a, o, c: Verification(Path(o.data["path"]).is_dir(), "folder exists")))  # type: ignore[index]
    reg.register(Tool(
        "fs_move", "Move a file or folder within approved directories. Never overwrites.",
        obj({"source": path, "destination": S("Full destination path including the new name")}, ["source", "destination"]),
        _move, Level.CONFIRM, "filesystem", preflight=_move_pre, describe=_move_describe, verify=_move_verify))
    reg.register(Tool(
        "fs_rename", "Rename a file or folder in place. Never overwrites.",
        obj({"path": path, "new_name": S("New file name only, no slashes", maxLength=255)}, ["path", "new_name"]),
        _rename, Level.CONFIRM, "filesystem", preflight=_rename_pre, describe=_rename_describe, verify=_move_verify))
    reg.register(Tool(
        "fs_write", "Create a new text file, or replace an existing one with overwrite=true (a backup copy is kept).",
        obj({"path": path, "content": S("File content", maxLength=MAX_WRITE_CHARS),
             "overwrite": B("Replace the file if it exists", default=False)}, ["path", "content"]),
        _write, Level.CONFIRM, "filesystem", preflight=_write_pre, describe=_write_describe, verify=_write_verify))
