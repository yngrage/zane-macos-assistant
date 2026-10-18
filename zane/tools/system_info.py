"""Real system information from real commands. Parsers are separate functions so they can be tested against
captured output; on macOS they are verified by `python -m zane doctor`, which prints raw output next to the parse."""
from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from typing import Any, Dict, List

from zane.security.permissions import Level
from zane.tools.errors import ToolError, raise_for_cmd
from zane.tools.registry import Tool, ToolContext, ToolOutcome, ToolRegistry
from zane.tools.schema import I, obj

_PRESSURE = {"1": "normal", "2": "warning", "4": "critical"}


# ---- parsers -----------------------------------------------------------------------------------
def parse_battery(text: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"source": None, "percent": None, "state": None, "remaining": None}
    m = re.search(r"Now drawing from '([^']+)'", text)
    if m:
        info["source"] = m.group(1)
    for line in text.splitlines():
        m = re.search(r"(\d{1,3})%;\s*([^;]+?)\s*(?:;\s*(.*))?$", line)
        if m:
            info["percent"], info["state"] = int(m.group(1)), m.group(2).strip()
            rest = m.group(3) or ""
            t = re.search(r"(\d+:\d{2}) remaining", rest)
            info["remaining"] = t.group(1) if t else ("calculating" if "no estimate" in rest else None)
            break
    return info


def format_battery(info: Dict[str, Any]) -> str:
    detail = [info["state"]] if info["state"] else []
    if info["remaining"] == "calculating":
        detail.append("estimate still calculating")
    elif info["remaining"]:
        detail.append(f"{info['remaining']} remaining")
    out = f"Battery {info['percent']}%" + (" - " + ", ".join(detail) if detail else "")
    return out + (f" (power source: {info['source']})" if info["source"] else "")


def parse_swapusage(text: str) -> Dict[str, float]:
    """'total = 1024.00M  used = 311.25M  free = 712.75M' -> MB."""
    scale = {"K": 1 / 1024, "M": 1.0, "G": 1024.0}
    return {k: float(v) * scale[u] for k, v, u in re.findall(r"(total|used|free)\s*=\s*([\d.]+)([KMG])", text)}


def parse_free_percent(text: str) -> Any:
    m = re.search(r"System-wide memory free percentage:\s*(\d+)%", text)
    return int(m.group(1)) if m else None


def parse_ps(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            rows.append({"pid": int(parts[0]), "cpu": float(parts[1]), "mem": float(parts[2]),
                         "rss_mb": int(parts[3]) / 1024, "command": os.path.basename(parts[4].strip()) or parts[4].strip()})
        except ValueError:
            continue
    return rows


# ---- tools -------------------------------------------------------------------------------------
def _battery(args, ctx: ToolContext) -> ToolOutcome:
    res = ctx.run(["pmset", "-g", "batt"], timeout=8)
    raise_for_cmd(res, "pmset")
    info = parse_battery(res.stdout)
    if info["percent"] is None:
        return ToolOutcome("No battery reported (desktop Mac, or the battery is not responding).", data=info)
    return ToolOutcome(format_battery(info), data=info)


def _disk(args, ctx: ToolContext) -> ToolOutcome:
    home = os.path.expanduser("~")
    u = shutil.disk_usage(home)
    gb = 1e9  # macOS reports decimal gigabytes
    data = {"total_gb": u.total / gb, "free_gb": u.free / gb, "free_pct": 100 * u.free / u.total}
    return ToolOutcome(
        f"Storage on the home volume: {data['free_gb']:.1f} GB free of {data['total_gb']:.1f} GB ({data['free_pct']:.0f}% free).",
        data=data,
    )


def _memory(args, ctx: ToolContext) -> ToolOutcome:
    total = ctx.run(["sysctl", "-n", "hw.memsize"], timeout=5)
    level = ctx.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], timeout=5)
    swap = ctx.run(["sysctl", "-n", "vm.swapusage"], timeout=5)
    mp = ctx.run(["memory_pressure"], timeout=10)
    data: Dict[str, Any] = {}
    parts: List[str] = []
    if level.ok and level.stdout.strip() in _PRESSURE:
        data["pressure"] = _PRESSURE[level.stdout.strip()]
        parts.append(f"Memory pressure: {data['pressure']}")
    if total.ok and total.stdout.strip().isdigit():
        data["ram_gb"] = int(total.stdout.strip()) / 2**30
        parts.append(f"RAM {data['ram_gb']:.1f} GB")
    if swap.ok:
        sw = parse_swapusage(swap.stdout)
        if "used" in sw and "total" in sw:
            data["swap_used_mb"], data["swap_total_mb"] = sw["used"], sw["total"]
            parts.append(f"swap used {sw['used']:.0f} MB of {sw['total']:.0f} MB")
    if mp.ok:
        pct = parse_free_percent(mp.stdout)
        if pct is not None:
            data["free_pct"] = pct
            parts.append(f"system-wide free memory {pct}%")
    if not parts:
        raise ToolError("Could not read memory statistics (sysctl / memory_pressure unavailable).")
    return ToolOutcome(" | ".join(parts), data=data)


def _processes(args, ctx: ToolContext) -> ToolOutcome:
    res = ctx.run(["ps", "-Ao", "pid,pcpu,pmem,rss,comm"], timeout=10)
    raise_for_cmd(res, "ps")
    rows = parse_ps(res.stdout)
    if not rows:
        raise ToolError("Could not parse the process list.")
    n = args["limit"]
    by_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:n]
    by_mem = sorted(rows, key=lambda r: r["rss_mb"], reverse=True)[:n]
    lines = ["Top by CPU:"] + [f"  {r['cpu']:5.1f}%  {r['command']} (pid {r['pid']})" for r in by_cpu]
    lines += ["Top by memory:"] + [f"  {r['rss_mb']:7.0f} MB  {r['command']} (pid {r['pid']})" for r in by_mem]
    lines.append("Note: ps CPU figures are averages, not live load; Activity Monitor shows instantaneous usage.")
    return ToolOutcome("\n".join(lines), data={"top_cpu": by_cpu, "top_mem": by_mem})


def _dev_env(args, ctx: ToolContext) -> ToolOutcome:
    lines: List[str] = []
    flags: List[str] = []
    machine = platform.machine()
    lines.append(f"ZANE runs on Python {sys.version.split()[0]} ({machine}) at {sys.executable}")
    if ctx.is_macos:
        hw = ctx.run(["sysctl", "-n", "hw.optional.arm64"], timeout=5)
        if hw.ok and hw.stdout.strip() == "1" and machine != "arm64":
            flags.append("This Mac is Apple Silicon but Python is running as x86_64 (Rosetta). Use an arm64 Python.")
    for name in ("python3", "git", "brew", "node"):
        path = ctx.which(name)
        if not path:
            lines.append(f"{name}: not found")
            continue
        res = ctx.run([path, "--version"], timeout=8)
        ver = (res.stdout or res.stderr).strip().splitlines()[0] if res.ok and (res.stdout or res.stderr).strip() else "version unknown"
        lines.append(f"{name}: {ver} ({path})")
        if name == "python3" and path == "/usr/bin/python3":
            flags.append("python3 is Apple's system Python; install a current one (brew install python@3.12) for a clean environment.")
    if ctx.is_macos:
        clt = ctx.run(["xcode-select", "-p"], timeout=5)
        if clt.ok:
            lines.append(f"Xcode Command Line Tools: {clt.stdout.strip()}")
        else:
            lines.append("Xcode Command Line Tools: not installed")
            flags.append("Install the command line tools: xcode-select --install")
    if flags:
        lines.append("Flags:")
        lines += ["  - " + f for f in flags]
    return ToolOutcome("\n".join(lines), data={"flags": flags})


def register(reg: ToolRegistry) -> None:
    mac = ("darwin",)
    reg.register(Tool("sys_battery", "Report battery percentage, charging state and time remaining (macOS).", obj(),
                      _battery, Level.SAFE, "system", platforms=mac, timeout_s=10))
    reg.register(Tool("sys_disk", "Report free and total storage on the home volume.", obj(), _disk, Level.SAFE, "system"))
    reg.register(Tool("sys_memory", "Report memory pressure, RAM size and swap usage (macOS).", obj(),
                      _memory, Level.SAFE, "system", platforms=mac, timeout_s=20))
    reg.register(Tool("sys_processes", "List the processes using the most CPU and memory.",
                      obj({"limit": I("Rows per list", minimum=1, maximum=15, default=5)}), _processes, Level.SAFE, "system", timeout_s=15))
    reg.register(Tool("sys_dev_env", "Check the development environment: Python, git, Homebrew, Node, Xcode command line tools, architecture.",
                      obj(), _dev_env, Level.SAFE, "system", timeout_s=40))
