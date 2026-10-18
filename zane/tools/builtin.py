"""Composition of the default tool set."""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from zane.tools import applications, development, filesystem, memory_tools, system_info, task_tools
from zane.tools.paths import PathGuard
from zane.tools.registry import ToolContext, ToolRegistry
from zane.tools.runner import run_cmd


def build_registry(
    config: Any,
    *,
    memory: Any,
    tasks: Any,
    policy: Any,
    approver: Any,
    audit: Any,
    runner: Callable[..., Any] = run_cmd,
    platform: str = sys.platform,
    now: Callable[[], datetime] = datetime.now,
    state: Any = None,
    sleep: Optional[Callable[[float], None]] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    app_dirs: Optional[Sequence[Any]] = None,
) -> ToolRegistry:
    guard = PathGuard(lambda: list(config.approved_dirs) + memory.project_paths())
    extra = {}
    if sleep is not None:
        extra["sleep"] = sleep
    if which is not None:
        extra["which"] = which
    ctx = ToolContext(
        config=config, guard=guard, runner=runner, memory=memory, tasks=tasks, platform=platform, now=now,
        app_dirs=tuple(app_dirs) if app_dirs is not None else tuple(applications.default_app_dirs()), **extra,
    )
    reg = ToolRegistry(ctx, policy, approver, audit, state=state)
    for module in (applications, filesystem, system_info, development, memory_tools, task_tools):
        module.register(reg)
    return reg
