from __future__ import annotations

import threading


class Cancelled(Exception):
    """Raised when the user cancels the current operation."""


class CancelToken:
    """Cooperative cancellation. Long-running work polls this; subprocesses are killed when it fires."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled()
