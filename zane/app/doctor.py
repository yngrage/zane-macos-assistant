"""`zane doctor`: a real environment audit (Phase 1 in miniature) plus a smoke test of every system tool.
With raw=True it prints the raw command output next to ZANE's parse, so the macOS parsers can be verified
against a real Mac in seconds."""
from __future__ import annotations

import os
import platform as pf
import sys
from typing import Any, List

from zane import __version__


def _first(text: str, n: int = 2) -> str:
    return " / ".join(l.strip() for l in text.strip().splitlines()[:n])


def run_doctor(app: Any, *, raw: bool = False) -> str:
    cfg, ctx = app.config, app.registry.ctx
    L: List[str] = [f"ZANE {__version__} - environment report", ""]
    L.append("System")
    L.append(f"  platform   : {pf.platform()} ({pf.machine()})")
    L.append(f"  python     : {sys.version.split()[0]} at {sys.executable}" + ("  [venv]" if sys.prefix != sys.base_prefix else ""))
    if sys.version_info < (3, 10):
        L.append("  warning    : Python < 3.10; ZANE targets 3.9+ but 3.11/3.12 is recommended")
    if ctx.is_macos:
        for label, argv in (("macOS", ["sw_vers", "-productVersion"]), ("chip", ["sysctl", "-n", "machdep.cpu.brand_string"]),
                            ("RAM bytes", ["sysctl", "-n", "hw.memsize"]), ("rosetta", ["sysctl", "-n", "sysctl.proc_translated"])):
            r = ctx.run(argv, timeout=5)
            L.append(f"  {label:<10} : {r.stdout.strip() if r.ok else '(unavailable)'}")
    else:
        L.append("  note       : not macOS - macOS-only tools (apps, battery, memory) report 'unavailable' here")
    L.append("")
    L.append("ZANE")
    L.append(f"  data dir   : {cfg.data_dir} ({'writable' if os.access(cfg.data_dir, os.W_OK) else 'NOT writable'})")
    L.append(f"  database   : {cfg.db_path} ({cfg.db_path.stat().st_size if cfg.db_path.exists() else 0} bytes)")
    L.append(f"  model      : {cfg.model} (provider: {cfg.provider})")
    L.append(f"  API key    : {'configured via ' + cfg.api_key_source if cfg.api_key else 'NOT configured (offline mode: direct commands only)'}")
    L.append(f"  fast path  : {'on' if cfg.fast_path else 'off'}   reminders: {'on' if cfg.reminders else 'off'}")
    roots = [str(p) for p in cfg.approved_dirs] + [r["path"] for r in app.memory.projects()]
    for r in roots:
        L.append(f"  approved   : {r}{'' if os.path.isdir(os.path.expanduser(r)) else '  (does not exist)'}")
    L.append(f"  permissions: {', '.join(app.policy.enabled())}")
    L.append("")
    L.append("Tool smoke test (read-only tools run for real)")
    for name, args in (("sys_dev_env", {}), ("sys_disk", {}), ("sys_battery", {}), ("sys_memory", {}), ("sys_processes", {"limit": 3})):
        res = app.registry.execute(name, args)
        L.append(f"  {name:<14}: {res.status:<11} {_first(res.output or res.error)}")
    if raw and ctx.is_macos:
        L += ["", "Raw command output (compare with the parsed lines above)"]
        for argv in (["pmset", "-g", "batt"], ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], ["sysctl", "-n", "vm.swapusage"],
                     ["memory_pressure"], ["ps", "-Ao", "pid,pcpu,pmem,rss,comm"]):
            r = ctx.run(argv, timeout=15)
            L.append(f"--- {' '.join(argv)} (exit {r.returncode})")
            L.extend((r.stdout or r.stderr or r.error).strip().splitlines()[:8])
    return "\n".join(L)
