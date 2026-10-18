"""Development assistance: inspect a project, check dependencies against its venv (by reading files, not
running code), read git state, and run an approved test command after confirmation."""
from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from zane.security.permissions import Level
from zane.tools.errors import ToolError, ToolRefusal, raise_for_cmd
from zane.tools.filesystem import SKIP_DIRS
from zane.tools.registry import ApprovalDetails, Tool, ToolContext, ToolOutcome, ToolRegistry, Verification
from zane.tools.schema import B, S, obj

PY_MARKERS = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile")
OTHER_MARKERS = ("package.json", "Cargo.toml", "go.mod", "Makefile", "README.md", "README")
VENV_NAMES = (".venv", "venv", "env")


def find_venv(root: Path) -> Optional[Path]:
    for name in VENV_NAMES:
        py = root / name / "bin" / "python"
        if py.exists() or py.is_symlink():
            return root / name
    return None


def venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def venv_version(venv: Path) -> Optional[str]:
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^version(?:_info)?\s*=\s*([\d.]+)", text, re.M)
    return m.group(1) if m else None


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names: List[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._\-]*)", line.split(";", 1)[0])
        if m:
            names.append(m.group(1))
    return names


def installed_packages(venv: Path) -> Set[str]:
    out: Set[str] = set()
    for sp in venv.glob("lib/python*/site-packages"):
        try:
            for d in sp.iterdir():
                if d.suffix in (".dist-info", ".egg-info"):
                    m = re.match(r"(.+?)-\d", d.stem)
                    out.add(_norm(m.group(1) if m else d.stem))
        except OSError:
            continue
    return out


def scan_files(root: Path, max_entries: int = 5000) -> Tuple[int, "Counter[str]"]:
    count, exts = 0, Counter()
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            count += 1
            exts[Path(f).suffix.lower() or "(none)"] += 1
            if count >= max_entries:
                return count, exts
    return count, exts


def inspect_project(root: Path) -> Dict[str, Any]:
    markers = [m for m in PY_MARKERS + OTHER_MARKERS if (root / m).exists()]
    is_python = any(m in markers for m in PY_MARKERS)
    venv = find_venv(root)
    reqs = parse_requirements(root / "requirements.txt")
    installed = installed_packages(venv) if venv else None
    missing = [r for r in reqs if _norm(r) not in installed] if installed is not None else None
    count, exts = scan_files(root)
    flags: List[str] = []
    if is_python and venv is None:
        flags.append("No virtual environment (.venv, venv or env) found. Create one: python3 -m venv .venv")
    if missing:
        flags.append(f"{len(missing)} requirement(s) not installed in {venv.name if venv else 'venv'}: " + ", ".join(missing[:8]))
    if not (root / ".git").exists():
        flags.append("Not a git repository.")
    if not any((root / d).is_dir() for d in ("tests", "test")):
        flags.append("No tests/ directory found.")
    return {"markers": markers, "python": is_python, "venv": str(venv) if venv else None,
            "venv_version": venv_version(venv) if venv else None, "requirements": reqs, "missing": missing,
            "files": count, "exts": exts.most_common(5), "flags": flags}


def _project_root(ctx: ToolContext, raw: str) -> Path:
    p = ctx.guard.resolve(raw)
    if not p.is_dir():
        raise ToolRefusal(f"{p} is not a directory.")
    return p


def _inspect(args, ctx: ToolContext) -> ToolOutcome:
    root = _project_root(ctx, args["path"])
    info = inspect_project(root)
    try:
        top = sorted(n + ("/" if (root / n).is_dir() else "") for n in os.listdir(root) if not n.startswith(".") or n == ".env.example")
    except OSError as exc:
        raise ToolError(str(exc))
    lines = [f"Project: {root}",
             "Markers: " + (", ".join(info["markers"]) or "none recognised"),
             "Top level: " + ", ".join(top[:25]) + (" ..." if len(top) > 25 else ""),
             f"Files scanned: {info['files']}" + (" (" + ", ".join(f"{e} {n}" for e, n in info["exts"]) + ")" if info["exts"] else "")]
    if info["venv"]:
        lines.append(f"Virtualenv: {Path(info['venv']).name}" + (f" (Python {info['venv_version']})" if info["venv_version"] else ""))
    if info["requirements"]:
        miss = info["missing"]
        lines.append(f"Requirements: {len(info['requirements'])} declared" + ("" if miss is None else f", {len(miss)} missing from the venv"))
    if info["flags"]:
        lines.append("Flags:")
        lines += ["  - " + f for f in info["flags"]]
    return ToolOutcome("\n".join(lines), data=info, untrusted=True)


def _git(ctx: ToolContext, root: Path, *git_args: str):
    # fsmonitor is disabled and optional locks off so inspecting an untrusted repo cannot run hooks or write to it.
    return ctx.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *git_args], timeout=15,
                   env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})


def _git_status(args, ctx: ToolContext) -> ToolOutcome:
    root = _project_root(ctx, args["path"])
    res = _git(ctx, root, "status", "--short", "--branch")
    raise_for_cmd(res, "git status")
    log = _git(ctx, root, "log", "-n", "5", "--oneline", "--no-decorate")
    out = res.stdout.strip()
    if log.ok and log.stdout.strip():
        out += "\n\nRecent commits:\n" + log.stdout.strip()
    return ToolOutcome(out, untrusted=True)


def _git_diff(args, ctx: ToolContext) -> ToolOutcome:
    root = _project_root(ctx, args["path"])
    argv = ["diff", "--no-ext-diff", "--no-textconv"] + (["--cached"] if args["staged"] else []) + (["--stat"] if args["stat_only"] else [])
    res = _git(ctx, root, *argv)
    raise_for_cmd(res, "git diff")
    return ToolOutcome(res.stdout.strip() or "No differences.", untrusted=True)


def _tests_prepare(ctx: ToolContext, args) -> Tuple[Path, Path, List[str]]:
    root = _project_root(ctx, args["path"])
    venv = find_venv(root)
    if venv is None:
        raise ToolRefusal("This project has no virtual environment (.venv, venv or env). ZANE will not run its tests with a "
                          "different interpreter. Create one first: python3 -m venv .venv")
    py = venv_python(venv)
    if args["runner"] == "pytest":
        argv = [str(py), "-m", "pytest", "-q", "--maxfail=20", "-p", "no:cacheprovider"]
    else:
        argv = [str(py), "-m", "unittest", "discover", "-q"]
    return root, py, argv


def _summarize(runner: str, text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if runner == "pytest":
        for l in reversed(lines):
            if re.search(r"\b\d+ (passed|failed|errors?|skipped|xfailed|deselected)\b|no tests ran", l):
                return l.strip("= ").strip()
        return lines[-1] if lines else "no output"
    ran = next((l for l in lines if re.match(r"Ran \d+ tests?", l)), "")
    status = next((l for l in reversed(lines) if l.startswith(("OK", "FAILED"))), "")
    return f"{ran} - {status}".strip(" -") or (lines[-1] if lines else "no output")


def _tests_pre(args, ctx: ToolContext) -> None:
    _tests_prepare(ctx, args)


def _tests_describe(args, ctx: ToolContext) -> ApprovalDetails:
    root, py, argv = _tests_prepare(ctx, args)
    return ApprovalDetails(f"Run the project's tests ({args['runner']})", f"{root} using {py}",
                           "Executes the project's own test code with your user permissions - it can read and write your files. "
                           "Output is captured; 5-minute limit.")


def _tests(args, ctx: ToolContext) -> ToolOutcome:
    root, py, argv = _tests_prepare(ctx, args)
    res = ctx.run(argv, cwd=root, timeout=ctx.timeout_s)
    raise_for_cmd(res, "test run", allow_nonzero=True)
    combined = (res.stdout + "\n" + res.stderr).strip()
    summary = _summarize(args["runner"], combined)
    tail = "\n".join(combined.splitlines()[-40:])
    if res.returncode == 0:
        return ToolOutcome(f"Tests passed: {summary}", data={"summary": summary, "exit": 0}, untrusted=True)
    if args["runner"] == "pytest" and res.returncode == 5:
        raise ToolError("No tests were collected.", output=tail, untrusted=True)
    raise ToolError(f"Tests failed (exit {res.returncode}): {summary}", output=tail, untrusted=True)


def _verify_tests(args, outcome: ToolOutcome, ctx: ToolContext) -> Verification:
    summary = (outcome.data or {}).get("summary", "")
    if re.search(r"\bpassed\b", summary) or summary.rstrip().endswith("OK"):
        return Verification(True, summary)
    return Verification(None, "exit status 0 but no pass summary was found in the output")


def register(reg: ToolRegistry) -> None:
    path = S("Absolute path of a project folder inside an approved directory")
    reg.register(Tool("dev_project_inspect",
                      "Inspect a project folder: type, structure, virtualenv, declared vs installed requirements, tests, git, and problems.",
                      obj({"path": path}, ["path"]), _inspect, Level.SAFE, "development", timeout_s=20))
    reg.register(Tool("dev_git_status", "Read-only git status and the last 5 commits for a project.",
                      obj({"path": path}, ["path"]), _git_status, Level.SAFE, "development", timeout_s=30))
    reg.register(Tool("dev_git_diff", "Read-only git diff (stat only by default) for reviewing changes.",
                      obj({"path": path, "staged": B("Show staged changes", default=False),
                           "stat_only": B("Only the per-file summary", default=True)}, ["path"]),
                      _git_diff, Level.SAFE, "development", timeout_s=30))
    reg.register(Tool("dev_run_tests",
                      "Run the project's tests with its own virtualenv (pytest or unittest). Needs the user's approval.",
                      obj({"path": path, "runner": S("Test runner", enum=["pytest", "unittest"], default="pytest")}, ["path"]),
                      _tests, Level.CONFIRM, "development", timeout_s=300,
                      preflight=_tests_pre, describe=_tests_describe, verify=_verify_tests))
