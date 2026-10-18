from __future__ import annotations

import threading
from enum import Enum
from typing import Callable, List


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    PLANNING = "planning"
    EXECUTING = "executing"
    SPEAKING = "speaking"
    AWAITING_APPROVAL = "awaiting_approval"
    ERROR = "error"


class StateTracker:
    """Single source of truth for what ZANE is doing. UIs subscribe to it, so nothing
    animates unless this actually changes."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = State.IDLE
        self._detail = ""
        self._subs: List[Callable[[State, str], None]] = []

    @property
    def state(self) -> State:
        return self._state

    @property
    def detail(self) -> str:
        return self._detail

    def set(self, state: State, detail: str = "") -> None:
        with self._lock:
            self._state, self._detail = state, detail
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(state, detail)
            except Exception:  # a broken subscriber must never break the assistant
                pass

    def subscribe(self, cb: Callable[[State, str], None]) -> None:
        with self._lock:
            self._subs.append(cb)
