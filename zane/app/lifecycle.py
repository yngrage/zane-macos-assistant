"""Composition root: wires config, storage, policy, tools, model and assistant together; sets up logging."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from zane.ai.cloud_provider import AnthropicProvider
from zane.ai.model_provider import UsageTracker
from zane.app.config import Config, ensure_private_dir
from zane.core.assistant import Assistant
from zane.core.intent_router import IntentRouter
from zane.core.state import StateTracker
from zane.memory.database import Database
from zane.memory.memory_service import MemoryService
from zane.security.approvals import DenyAllApprover
from zane.security.audit_log import AuditLog
from zane.security.permissions import PermissionPolicy
from zane.security.redaction import redact
from zane.tasks.reminders import ChainNotifier, ConsoleNotifier, MacNotifier, ReminderScheduler
from zane.tasks.task_manager import TaskManager
from zane.tools.builtin import build_registry
from zane.tools.registry import ToolRegistry
from zane.tools.runner import run_cmd

_DEFAULT = object()


class RedactingFilter(logging.Filter):
    """Nothing that looks like a credential reaches the log file."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging(config: Config) -> logging.Logger:
    logger = logging.getLogger("zane")
    logger.setLevel(getattr(logging, config.log_level, logging.INFO))
    for h in list(logger.handlers):
        if getattr(h, "_zane", False):
            logger.removeHandler(h)
            h.close()
    ensure_private_dir(config.data_dir)
    ensure_private_dir(config.log_dir)
    handler = logging.handlers.RotatingFileHandler(config.log_dir / "zane.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler._zane = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class App:
    config: Config
    db: Database
    memory: MemoryService
    tasks: TaskManager
    policy: PermissionPolicy
    audit: AuditLog
    registry: ToolRegistry
    assistant: Assistant
    state: StateTracker
    tracker: UsageTracker
    approver: Any
    scheduler: Optional[ReminderScheduler] = None

    def close(self) -> None:
        if self.scheduler:
            self.scheduler.stop()
        self.db.close()


def build_app(
    config: Config,
    *,
    approver: Any = None,
    provider: Any = _DEFAULT,
    runner: Callable[..., Any] = run_cmd,
    platform: str = sys.platform,
    now: Callable[[], datetime] = datetime.now,
    notifier: Any = None,
    **registry_overrides: Any,
) -> App:
    """provider: omit for the configured cloud model, or pass None for offline-only operation."""
    ensure_private_dir(config.data_dir)
    db = Database(config.db_path)
    memory = MemoryService(db, clock=now)
    tasks = TaskManager(db, clock=now)
    policy = PermissionPolicy(db)
    audit = AuditLog(db, clock=now)
    state = StateTracker()
    approver = approver or DenyAllApprover()
    registry = build_registry(config, memory=memory, tasks=tasks, policy=policy, approver=approver, audit=audit,
                              runner=runner, platform=platform, now=now, state=state, **registry_overrides)
    if provider is _DEFAULT:
        provider = AnthropicProvider(config.api_key, config.model, timeout_s=config.request_timeout_s, max_retries=config.max_retries)
    tracker = UsageTracker()
    assistant = Assistant(config=config, registry=registry, provider=provider, router=IntentRouter(), memory=memory, state=state,
                          clock=now, platform_name="macOS" if platform == "darwin" else platform, tracker=tracker)
    scheduler = None
    if config.reminders:
        scheduler = ReminderScheduler(tasks, notifier or ChainNotifier([MacNotifier(runner), ConsoleNotifier()] if platform == "darwin" else [ConsoleNotifier()]))
    return App(config, db, memory, tasks, policy, audit, registry, assistant, state, tracker, approver, scheduler)
