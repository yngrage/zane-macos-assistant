"""Provider-neutral message and tool types. Vendors are adapters (cloud_provider.py), never imported by the core."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolResultMessage:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: str  # "user" | "assistant"
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_results: List[ToolResultMessage] = field(default_factory=list)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelResponse:
    text: str
    tool_calls: List[ToolCall]
    stop_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class ProviderError(Exception):
    """kind: auth | network | timeout | rate_limit | server | bad_request | not_configured | unknown"""

    def __init__(self, kind: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class ModelProvider(ABC):
    name = "provider"
    model = ""

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def complete(
        self, *, system: str, messages: List[Message], tools: List[ToolSpec], max_tokens: int = 1500
    ) -> ModelResponse: ...


class UsageTracker:
    def __init__(self) -> None:
        self.calls = 0
        self.errors = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage: Usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    def error(self) -> None:
        self.errors += 1

    def summary(self) -> str:
        return (
            f"{self.calls} model call(s), {self.input_tokens} input / {self.output_tokens} output tokens, "
            f"{self.errors} error(s) this session"
        )
