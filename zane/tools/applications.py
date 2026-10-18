"""Application control. Apps are resolved against what is actually installed, so model text never reaches
`open`/`osascript` unvalidated; app names are passed as separate argv items, never spliced into script source."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from zane.security.permissions import Level
from zane.tools.errors import ToolError, ToolRefusal, raise_for_cmd
from zane.tools.paths import sensitive_reason
from zane.tools.registry import ApprovalDetails, Tool, ToolContext, ToolOutcome, ToolRegistry, Verification
from zane.tools.schema import S, obj

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+&'()\-]{0,63}$")
ALIASES = {
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "code": "Visual Studio Code",
    "chrome": "Google Chrome", "settings": "System Settings", "system preferences": "System Settings",
    "preferences": "System Settings", "term": "Terminal",
}
FOLDERS = {"downloads": "Downloads", "desktop": "Desktop", "documents": "Documents", "home": ""}
# Opening these with an app (Terminal in particular) can EXECUTE them, so they are refused.
LAUNCHABLE_SUFFIXES = {".command", ".tool", ".sh", ".zsh", ".bash", ".app", ".pkg", ".dmg", ".scpt", ".scptd",
                       ".workflow", ".terminal", ".action", ".jar", ".pl", ".rb"}


def default_app_dirs() -> List[Path]:
    return [Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
            Path("/System/Applications/Utilities"), Path.home() / "Applications"]


def installed_apps(app_dirs: Sequence[Path]) -> Dict[str, Path]:
    apps: Dict[str, Path] = {}
    for d in app_dirs:
        try:
            entries = sorted(Path(d).iterdir())
        except OSError:
            continue
        for e in entries:
            if e.suffix == ".app" and e.is_dir():
                apps.setdefault(e.stem.lower(), e)
    return apps


def resolve_app(name: str, app_dirs: Sequence[Path]) -> Path:
    clean = " ".join(str(name).split())
    if not NAME_RE.match(clean):
        raise ToolRefusal("That is not a valid application name.")
    key = ALIASES.get(clean.lower(), clean).lower()
    apps = installed_apps(app_dirs)
    if key in apps:
        return apps[key]
    cands = [p for k, p in apps.items() if k.startswith(key)] or [p for k, p in apps.items() if key in k]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise ToolRefusal(f"No installed application matches '{clean}'.")
    raise ToolRefusal(f"'{clean}' is ambiguous: " + ", ".join(sorted(p.stem for p in cands)[:6]))


def is_running(ctx: ToolContext, app: Path) -> Optional[bool]:
    """True/False from the real process list, None if it cannot be read. Safari lives behind a cryptex path
    on recent macOS, so both the nominal and the resolved path are checked."""
    res = ctx.run(["ps", "-Ao", "comm"], timeout=8)
    if not res.ok:
        return None
    prefixes = tuple(f"{c}/Contents/MacOS/" for c in {str(app), os.path.realpath(app)})
    return any(line.strip().startswith(prefixes) for line in res.stdout.splitlines())


def _open_target(ctx: ToolContext, raw: str) -> Path:
    p = ctx.guard.resolve(raw)
    if not p.exists():
        raise ToolRefusal(f"{p} does not exist.")
    if p.is_file():
        if p.suffix.lower() in LAUNCHABLE_SUFFIXES or os.access(p, os.X_OK):
            raise ToolRefusal("Executable or launchable files are never opened through an app.")
        why = sensitive_reason(p)
        if why:
            raise ToolRefusal(f"Refusing to open {p.name}: {why}.")
    return p


def _open_pre(args, ctx: ToolContext) -> None:
    resolve_app(args["name"], ctx.app_dirs)
    if args.get("path"):
        _open_target(ctx, args["path"])


def _open(args, ctx: ToolContext) -> ToolOutcome:
    app = resolve_app(args["name"], ctx.app_dirs)
    argv = ["open", "-a", str(app)]
    if args.get("path"):
        argv.append(str(_open_target(ctx, args["path"])))
    raise_for_cmd(ctx.run(argv, timeout=15), "open")
    return ToolOutcome(f"Opened {app.stem}.", data={"app": app.stem, "app_path": str(app)})


def _verify_open(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    app = Path(outcome.data["app_path"])  # type: ignore[index]
    for _ in range(6):
        state = is_running(ctx, app)
        if state is None:
            return Verification(None, "could not read the process list")
        if state:
            return Verification(True, f"{app.stem} is running")
        ctx.sleep(0.5)
    return Verification(False, f"{app.stem} does not appear in the process list")


def _quit_pre(args, ctx: ToolContext) -> None:
    resolve_app(args["name"], ctx.app_dirs)


def _quit_describe(args, ctx: ToolContext) -> ApprovalDetails:
    app = resolve_app(args["name"], ctx.app_dirs)
    return ApprovalDetails("Quit application", app.stem,
                           "The app is asked to quit normally. Unsaved work may trigger a save prompt, or be lost if the app does not prompt.")


def _quit(args, ctx: ToolContext) -> ToolOutcome:
    app = resolve_app(args["name"], ctx.app_dirs)
    if is_running(ctx, app) is False:
        # AppleScript would launch a closed app just to quit it, so don't ask.
        return ToolOutcome(f"{app.stem} is not running.", data={"app": app.stem, "app_path": str(app), "already": True})
    res = ctx.run(["osascript", "-e", "on run argv", "-e", "tell application (item 1 of argv) to quit", "-e", "end run", app.stem], timeout=15)
    try:
        raise_for_cmd(res, "quit request")
    except ToolError as exc:
        raise ToolError(f"{exc}. If macOS asked, allow your terminal app to control {app.stem} "
                        "(System Settings > Privacy & Security > Automation).") from exc
    return ToolOutcome(f"Asked {app.stem} to quit.", data={"app": app.stem, "app_path": str(app)})


def _verify_quit(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    app = Path(outcome.data["app_path"])  # type: ignore[index]
    for _ in range(10):
        state = is_running(ctx, app)
        if state is None:
            return Verification(None, "could not read the process list")
        if state is False:
            return Verification(True, f"{app.stem} is no longer running")
        ctx.sleep(0.5)
    return Verification(False, f"{app.stem} is still running (it may be waiting on a save dialog)")


def _folder_target(ctx: ToolContext, args) -> Path:
    if args.get("location"):
        return Path.home() / FOLDERS[args["location"].lower()]
    if args.get("path"):
        p = ctx.guard.resolve(args["path"])
        if not p.is_dir():
            raise ToolRefusal(f"{p} is not a folder.")
        return p
    raise ToolRefusal("Provide either 'location' (downloads, desktop, documents, home) or 'path'.")


def _folder_open(args, ctx: ToolContext) -> ToolOutcome:
    target = _folder_target(ctx, args)
    raise_for_cmd(ctx.run(["open", str(target)], timeout=15), "open")
    return ToolOutcome(f"Opened {target} in Finder.", data={"path": str(target)})


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="app_open",
        description="Open an installed macOS application by name (e.g. Safari, Terminal, Visual Studio Code), optionally opening a folder or document from an approved directory with it.",
        schema=obj({"name": S("Application name", maxLength=64),
                    "path": S("Optional absolute path of an approved folder or document to open with the app")}, ["name"]),
        handler=_open, level=Level.SAFE, category="applications", platforms=("darwin",),
        preflight=_open_pre, verify=_verify_open, timeout_s=15,
    ))
    reg.register(Tool(
        name="app_quit",
        description="Ask a running macOS application to quit normally.",
        schema=obj({"name": S("Application name", maxLength=64)}, ["name"]),
        handler=_quit, level=Level.CONFIRM, category="applications", platforms=("darwin",),
        preflight=_quit_pre, describe=_quit_describe, verify=_verify_quit, timeout_s=15,
    ))
    reg.register(Tool(
        name="folder_open",
        description="Reveal a folder in Finder: Downloads, Desktop, Documents, home, or an approved directory.",
        schema=obj({"location": S("Well-known folder", enum=list(FOLDERS)), "path": S("Absolute path of an approved folder")}),
        handler=_folder_open, level=Level.SAFE, category="applications", platforms=("darwin",),
        preflight=lambda a, c: _folder_target(c, a), timeout_s=15,
    ))
