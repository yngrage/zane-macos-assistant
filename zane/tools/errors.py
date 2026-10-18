from __future__ import annotations

import functools
from typing import Any, Callable

from zane.core.cancel import Cancelled


class ToolRefusal(Exception):
    """An action refused by policy or validation before it ran (not a crash)."""


class ToolError(Exception):
    """An expected failure while running a tool. `output` may carry useful detail."""

    def __init__(self, message: str, output: str = "", untrusted: bool = False) -> None:
        super().__init__(message)
        self.output = output
        self.untrusted = untrusted


def user_errors(*exc_types: type) -> Callable:
    """Decorator: turn domain exceptions (TaskError, MemoryRejected, ...) into clean ToolErrors."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(args, ctx):
            try:
                return fn(args, ctx)
            except exc_types as exc:
                raise ToolError(str(exc)) from exc

        return inner

    return deco


def raise_for_cmd(res: Any, what: str, *, allow_nonzero: bool = False) -> None:
    """Map a CmdResult onto the registry's statuses (cancelled / timeout / failed)."""
    if res.cancelled:
        raise Cancelled()
    if res.timed_out:
        raise TimeoutError(f"{what} timed out")
    if res.error:
        raise ToolError(f"{what}: {res.error}")
    if not allow_nonzero and res.returncode != 0:
        tail = (res.stderr or res.stdout).strip().splitlines()
        raise ToolError(f"{what} failed (exit {res.returncode})" + (f": {tail[-1]}" if tail else ""))
