"""Reminders. DISCLOSURE: these live in ZANE's own database and fire only while ZANE is running.
(Registering with Apple Reminders - which survives ZANE quitting - is a planned, unbuilt feature.)"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, List, Optional, Sequence, Tuple

from zane.tasks.task_manager import Task, TaskManager
from zane.tools.runner import run_cmd

log = logging.getLogger("zane.reminders")

DISCLOSURE = "Stored in ZANE only: it will fire only while ZANE is running. It is not registered with Apple Reminders or Calendar."


class ConsoleNotifier:
    def __init__(self, output: Callable[[str], None] = print) -> None:
        self.output = output

    def notify(self, title: str, message: str) -> bool:
        self.output(f"\n[{title}] {message}")
        return True


class MacNotifier:
    """Native notification through osascript. The text goes in as argv items, never into script source."""

    def __init__(self, runner: Callable[..., object] = run_cmd) -> None:
        self.runner = runner

    def notify(self, title: str, message: str) -> bool:
        res = self.runner(
            ["osascript", "-e", "on run argv", "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
             "-e", "end run", message, title],
            timeout=8,
        )
        return bool(getattr(res, "ok", False))


class ChainNotifier:
    def __init__(self, notifiers: Sequence[object]) -> None:
        self.notifiers = list(notifiers)

    def notify(self, title: str, message: str) -> bool:
        for n in self.notifiers:
            try:
                if n.notify(title, message):  # type: ignore[attr-defined]
                    return True
            except Exception:
                log.exception("notifier failed")
        return False


class ReminderScheduler:
    def __init__(self, tasks: TaskManager, notifier: object, interval: float = 30.0) -> None:
        self.tasks = tasks
        self.notifier = notifier
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def check_once(self) -> List[Tuple[Task, bool]]:
        fired: List[Tuple[Task, bool]] = []
        for t in self.tasks.due_reminders():
            delivered = bool(self.notifier.notify("ZANE reminder", t.title))  # type: ignore[attr-defined]
            if delivered:
                self.tasks.mark_reminded(t.id)  # undelivered ones are retried on the next tick
            fired.append((t, delivered))
        return fired

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(self.interval):
                try:
                    self.check_once()
                except Exception:
                    log.exception("reminder check failed")

        self._thread = threading.Thread(target=loop, daemon=True, name="zane-reminders")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
