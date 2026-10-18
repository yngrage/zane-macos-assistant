"""The only way ZANE starts processes: argument arrays (never shell strings), no stdin, hard timeouts,
cancellation, output caps, and a scrubbed environment so secrets never leak into child processes."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

_SECRET_ENV = re.compile(r"(api[_-]?key|token|secret|passw|credential|^ANTHROPIC_)", re.I)


@dataclass
class CmdResult:
    returncode: Optional[int]
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled and not self.error


def scrub_env(extra: Optional[Mapping[str, str]] = None) -> dict:
    """os.environ minus anything that looks like a credential, plus explicit extras."""
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
    if extra:
        env.update(extra)
    return env


def _kill(proc: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def run_cmd(
    argv: Sequence[str],
    *,
    timeout: float = 15.0,
    cwd: Optional[object] = None,
    env: Optional[Mapping[str, str]] = None,
    cancel: Optional[object] = None,
    max_output: int = 200_000,
) -> CmdResult:
    if isinstance(argv, (str, bytes)) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of strings (shell strings are not accepted)")
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,  # type: ignore[arg-type]
            env=scrub_env(env),
            text=True,
            errors="replace",
            start_new_session=True,  # own process group so a kill takes children with it
        )
    except FileNotFoundError:
        return CmdResult(None, error=f"command not found: {argv[0]}")
    except OSError as exc:
        return CmdResult(None, error=f"could not start {argv[0]}: {exc}")
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=0.1)
            return CmdResult(proc.returncode, _trim(out, max_output), _trim(err, max_output))
        except subprocess.TimeoutExpired:
            cancelled = bool(cancel is not None and getattr(cancel, "cancelled", False))
            timed_out = time.monotonic() >= deadline
            if cancelled or timed_out:
                _kill(proc)
                out, err = proc.communicate()
                return CmdResult(
                    proc.returncode,
                    _trim(out, max_output),
                    _trim(err, max_output),
                    timed_out=timed_out and not cancelled,
                    cancelled=cancelled,
                )
