"""Approval requests. The text shown to the user is built from validated arguments by the tool's own
`describe` hook, never from model prose, and is stripped of control characters so it can't be spoofed."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional

try:  # Protocol is 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

_CTRL = re.compile("[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def clean(text: object, limit: int = 300) -> str:
    s = " ".join(_CTRL.sub(" ", str(text)).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


@dataclass
class ApprovalRequest:
    tool: str
    level: int
    action: str
    target: str = ""
    consequences: str = ""
    strict: bool = False


@dataclass
class ApprovalResult:
    approved: bool
    reason: str = ""


class Approver(Protocol):
    def request(self, req: ApprovalRequest) -> ApprovalResult: ...


class DenyAllApprover:
    """Default when nobody can answer: nothing needing approval ever runs."""

    def request(self, req: ApprovalRequest) -> ApprovalResult:
        return ApprovalResult(False, "no interactive approver available")


class ScriptedApprover:
    """Deterministic approver for tests."""

    def __init__(self, answers: Optional[List[bool]] = None, default: bool = False) -> None:
        self.answers = list(answers or [])
        self.default = default
        self.requests: List[ApprovalRequest] = []

    def request(self, req: ApprovalRequest) -> ApprovalResult:
        self.requests.append(req)
        ok = self.answers.pop(0) if self.answers else self.default
        return ApprovalResult(bool(ok), "scripted")


def format_request(req: ApprovalRequest) -> str:
    risk = " (HIGH RISK)" if req.level >= 3 else ""
    return "\n".join(
        [
            f"APPROVAL REQUIRED - Level {req.level}{risk}",
            f"  Action : {clean(req.action)}",
            f"  Target : {clean(req.target, 500)}",
            f"  Impact : {clean(req.consequences, 500)}",
        ]
    )


class CliApprover:
    """Terminal approval. Default answer is NO; EOF or Ctrl-C is NO."""

    def __init__(self, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print) -> None:
        self.input = input_fn
        self.output = output_fn

    def request(self, req: ApprovalRequest) -> ApprovalResult:
        self.output(format_request(req))
        try:
            if req.strict:
                ok = self.input("  Type CONFIRM to proceed (anything else cancels): ").strip() == "CONFIRM"
            else:
                ok = self.input("  Approve? [y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return ApprovalResult(False, "no answer")
        return ApprovalResult(ok, "approved by user" if ok else "declined by user")
