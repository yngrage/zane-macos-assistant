"""Anthropic Messages API adapter (tool use). Error classification is by exception name/status so it does not
depend on SDK internals, and messages are redacted so an API key can never appear in an error."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from zane.ai.model_provider import (
    Message, ModelProvider, ModelResponse, ProviderError, ToolCall, ToolSpec, Usage,
)
from zane.security.redaction import redact


def to_anthropic_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            for c in m.tool_calls:
                blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no content)"}]})
        else:
            blocks = [  # tool_result blocks must come first in the user turn
                {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                for r in m.tool_results
            ]
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            out.append({"role": "user", "content": blocks or [{"type": "text", "text": "(empty)"}]})
    return out


def classify_exception(exc: BaseException) -> Tuple[str, bool]:
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if name == "APITimeoutError" or isinstance(exc, TimeoutError):
        return "timeout", True
    if name == "APIConnectionError":
        return "network", True
    if status in (401, 403):
        return "auth", False
    if status == 429:
        return "rate_limit", True
    if isinstance(status, int) and status >= 500:
        return "server", True
    if isinstance(status, int) and 400 <= status < 500:
        return "bad_request", False
    return "unknown", False


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def parse_response(resp: Any, model: str) -> ModelResponse:
    text_parts: List[str] = []
    calls: List[ToolCall] = []
    for block in _get(resp, "content", []) or []:
        kind = _get(block, "type")
        if kind == "text":
            text_parts.append(_get(block, "text", "") or "")
        elif kind == "tool_use":
            calls.append(ToolCall(id=_get(block, "id"), name=_get(block, "name"), arguments=dict(_get(block, "input", {}) or {})))
    usage = _get(resp, "usage")
    return ModelResponse(
        text="".join(text_parts),
        tool_calls=calls,
        stop_reason=_get(resp, "stop_reason", "") or "",
        usage=Usage(int(_get(usage, "input_tokens", 0) or 0), int(_get(usage, "output_tokens", 0) or 0)),
        model=model,
    )


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, api_key: Any, model: str, *, timeout_s: float = 60.0, max_retries: int = 2, client: Any = None) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._client = client

    def is_configured(self) -> bool:
        return bool(self._api_key) or self._client is not None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ProviderError("not_configured", "The 'anthropic' package is not installed. Run bash scripts/setup.sh.") from exc
            self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout, max_retries=self._max_retries)
        return self._client

    def complete(self, *, system: str, messages: List[Message], tools: List[ToolSpec], max_tokens: int = 1500) -> ModelResponse:
        if not self.is_configured():
            raise ProviderError("not_configured", "No API key configured.")
        client = self._get_client()
        kwargs: Dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system, messages=to_anthropic_messages(messages))
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:
            kind, retryable = classify_exception(exc)
            raise ProviderError(kind, redact(f"{type(exc).__name__}: {exc}")[:300], retryable) from exc
        return parse_response(resp, self.model)
