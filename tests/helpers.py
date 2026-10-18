from types import SimpleNamespace

from zane.ai.model_provider import ModelProvider, ModelResponse, ToolCall, Usage
from zane.tools.runner import CmdResult


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        from datetime import timedelta

        self.now = self.now + timedelta(**kw)


class FakeRunner:
    """Records argv arrays and returns canned CmdResults, so macOS-only tools can be tested on any OS.
    Later rules win. Unknown commands behave like 'command not found'."""

    def __init__(self):
        self.rules = []
        self.calls = []
        self.kwargs = []

    def when(self, prefix, stdout="", stderr="", returncode=0, error=""):
        self.rules.append((list(prefix), CmdResult(returncode, stdout, stderr, error=error)))
        return self

    def on(self, prefix, fn):
        self.rules.append((list(prefix), fn))
        return self

    def __call__(self, argv, *, timeout=15, cwd=None, env=None, cancel=None, **kw):
        assert isinstance(argv, (list, tuple)) and all(isinstance(a, str) for a in argv), "argv must be a list of strings"
        self.calls.append(list(argv))
        self.kwargs.append({"cwd": cwd, "env": env, "timeout": timeout})
        for prefix, res in reversed(self.rules):
            if list(argv[: len(prefix)]) == prefix:
                return res(argv) if callable(res) else res
        return CmdResult(None, error=f"command not found: {argv[0]}")


class FakeProvider(ModelProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self, script=None, configured=True):
        self.script = list(script or [])
        self.calls = []
        self.configured = configured

    def is_configured(self):
        return self.configured

    def complete(self, *, system, messages, tools, max_tokens=1500):
        self.calls.append(SimpleNamespace(system=system, messages=list(messages), tools=tools))
        if not self.script:
            raise AssertionError("provider was called but no scripted response is left")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(system, messages, tools)
        return item


def text(t):
    return ModelResponse(t, [], "end_turn", Usage(10, 5), "fake-1")


def call(name, args=None, id="call_1"):
    return ModelResponse("", [ToolCall(id, name, args or {})], "tool_use", Usage(10, 5), "fake-1")
