"""The assistant loop. Fast path for unambiguous commands; otherwise the model plans, calls validated tools,
and sees real results (including failures and verification outcomes) before it answers."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from zane.ai.model_provider import (
    Message, ModelProvider, ModelResponse, ProviderError, ToolResultMessage, UsageTracker,
)
from zane.core.cancel import CancelToken, Cancelled
from zane.core.context_manager import build_system_prompt, window
from zane.core.intent_router import IntentRouter, Route
from zane.core.state import State, StateTracker
from zane.tools.registry import ToolRegistry, ToolResult

log = logging.getLogger("zane.assistant")

OFFLINE_HELP = ("Without a model I can still run direct commands: open apps and folders, battery, storage, memory and "
                "process checks, tasks, plans, briefings and memory notes. Type /help for examples.")


@dataclass
class Reply:
    text: str
    route: str = "model"  # fast-path | model | offline | error | cancelled | busy
    intents: List[str] = field(default_factory=list)
    results: List[ToolResult] = field(default_factory=list)
    cancelled: bool = False


class Assistant:
    MAX_STEPS = 8

    def __init__(
        self,
        *,
        config: Any,
        registry: ToolRegistry,
        provider: Optional[ModelProvider],
        router: IntentRouter,
        memory: Any = None,
        state: Optional[StateTracker] = None,
        clock: Callable[[], datetime] = datetime.now,
        platform_name: str = "macOS",
        tracker: Optional[UsageTracker] = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.provider = provider
        self.router = router
        self.memory = memory
        self.state = state or StateTracker()
        self.clock = clock
        self.platform_name = platform_name
        self.tracker = tracker or UsageTracker()
        self.history: List[Message] = []
        self._busy = threading.Lock()

    def reset(self) -> None:
        self.history = []

    # ---- entry point ------------------------------------------------------------------------
    def handle(self, text: str, cancel: Optional[CancelToken] = None) -> Reply:
        text = (text or "").strip()
        if not text:
            return Reply("")
        if not self._busy.acquire(blocking=False):
            return Reply("Still working on your previous request. Cancel it first.", route="busy")
        cancel = cancel or CancelToken()
        start = len(self.history)
        self.state.set(State.PROCESSING)
        try:
            reply = self._handle(text, cancel)
            if reply.route != "error":
                self.state.set(State.IDLE)
            return reply
        except Cancelled:
            del self.history[start:]
            self.state.set(State.IDLE)
            return Reply("Cancelled. Anything that already ran is listed in /audit.", route="cancelled", cancelled=True)
        except Exception as exc:
            log.exception("assistant crashed")
            del self.history[start:]
            self.state.set(State.ERROR, str(exc))
            return Reply(f"Internal error: {type(exc).__name__}: {exc}. Details are in the log.", route="error")
        finally:
            self._busy.release()

    def _handle(self, text: str, cancel: CancelToken) -> Reply:
        route = self.router.match(text) if self.config.fast_path else None
        if route is not None:
            return self._run_route(text, route, cancel)
        if self.provider is None or not self.provider.is_configured():
            return Reply("No model is configured (set ANTHROPIC_API_KEY, or run `bash scripts/run.sh key set`). " + OFFLINE_HELP, route="offline")
        return self._run_model(text, cancel)

    # ---- fast path --------------------------------------------------------------------------
    def _run_route(self, text: str, route: Route, cancel: CancelToken) -> Reply:
        self.state.set(State.EXECUTING, route.tool)
        result = self.registry.execute(route.tool, route.args, cancel=cancel)
        cancel.raise_if_cancelled()
        body = result.to_user_text()
        self.history += [Message("user", text=text), Message("assistant", text=body[:4000])]
        return Reply(body, route="fast-path", intents=self._intents([result]), results=[result])

    # ---- model path -------------------------------------------------------------------------
    def _run_model(self, text: str, cancel: CancelToken) -> Reply:
        self.state.set(State.PLANNING)
        memories = self.memory.relevant_context(text, k=5) if self.memory is not None else []
        system = build_system_prompt(
            now=self.clock(), platform_name=self.platform_name,
            roots=[str(r) for r in self.registry.ctx.guard.roots()], memories=memories,
        )
        tools = self.registry.specs()
        start = len(self.history)
        self.history.append(Message("user", text=text))
        results: List[ToolResult] = []
        steps = 0
        try:
            while True:
                cancel.raise_if_cancelled()
                self.state.set(State.PROCESSING)
                resp = self._call_model(system, tools, cancel)
                self.tracker.add(resp.usage)
                self.history.append(Message("assistant", text=resp.text, tool_calls=list(resp.tool_calls)))
                if not resp.tool_calls:
                    final = resp.text.strip() or "(The model returned no text.)"
                    break
                steps += 1
                self.state.set(State.EXECUTING, ", ".join(c.name for c in resp.tool_calls))
                msgs: List[ToolResultMessage] = []
                for call in resp.tool_calls:
                    r = self.registry.execute(call.name, call.arguments, cancel=cancel)
                    results.append(r)
                    msgs.append(ToolResultMessage(call.id, r.to_model_text(), is_error=r.status not in ("ok", "unverified")))
                self.history.append(Message("user", tool_results=msgs))
                if steps >= self.MAX_STEPS:
                    final = (f"Stopped after {steps} tool rounds without reaching an answer. Steps taken:\n"
                             + "\n".join(f"- {r.tool}: {r.status}" for r in results))
                    self.history.append(Message("assistant", text=final))
                    break
        except ProviderError as exc:
            del self.history[start:]
            self.state.set(State.ERROR, exc.kind)
            ran = ("\nActions that ran before the connection failed:\n" + "\n".join(f"- {r.tool}: {r.status}" for r in results)) if results else ""
            return Reply(self._provider_failure(exc) + ran, route="error", results=results, intents=self._intents(results))
        return Reply(final, route="model", intents=self._intents(results), results=results)

    def _call_model(self, system: str, tools: list, cancel: CancelToken) -> ModelResponse:
        messages = window(self.history, self.config.history_turns)
        box: Dict[str, Any] = {}

        def work() -> None:
            try:
                box["resp"] = self.provider.complete(system=system, messages=messages, tools=tools, max_tokens=self.config.max_tokens)  # type: ignore[union-attr]
            except BaseException as exc:
                box["err"] = exc

        th = threading.Thread(target=work, daemon=True, name="zane-model")
        th.start()
        while th.is_alive():
            th.join(0.1)
            if cancel.cancelled:
                raise Cancelled()  # the request thread is abandoned; it ends on its own at the API timeout
        if "err" in box:
            if isinstance(box["err"], ProviderError):
                self.tracker.error()
            raise box["err"]
        return box["resp"]

    @staticmethod
    def _provider_failure(exc: ProviderError) -> str:
        k = exc.kind
        if k == "auth":
            return "The API key was rejected. Check it with `bash scripts/run.sh key status` or replace it with `bash scripts/run.sh key set`."
        if k == "not_configured":
            return f"{exc} " + OFFLINE_HELP
        if k in ("network", "timeout"):
            return "I can't reach the model (network or timeout). " + OFFLINE_HELP
        if k == "rate_limit":
            return "The model provider is rate limiting requests. Try again shortly."
        if k == "server":
            return "The model provider reported a server-side error. Try again shortly; direct commands still work."
        return f"The model request failed: {exc}"

    def _intents(self, results: List[ToolResult]) -> List[str]:
        if not results:
            return ["conversation"]
        out: List[str] = []
        for r in results:
            i = self.registry.intent_of(r.tool)
            if i not in out:
                out.append(i)
        if len(results) > 1:
            out.append("multi_step_workflow")
        if any(self.registry.level_of(r.tool) >= 2 for r in results):
            out.append("needs_confirmation")
        return out
