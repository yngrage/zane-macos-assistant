import json
import os
from types import SimpleNamespace as NS

import pytest

from zane.ai.cloud_provider import AnthropicProvider, classify_exception, parse_response, to_anthropic_messages
from zane.ai.model_provider import Message, ProviderError, ToolCall, ToolResultMessage, ToolSpec
from zane.app.config import Config, parse_dotenv
from zane.security.keychain import SERVICE, keychain_clear, keychain_get
from helpers import FakeRunner


# ---- provider --------------------------------------------------------------------------------
def test_messages_convert_to_the_anthropic_shape():
    msgs = [Message("user", text="hi"),
            Message("assistant", text="checking", tool_calls=[ToolCall("t1", "sys_disk", {})]),
            Message("user", tool_results=[ToolResultMessage("t1", "status: ok", False), ToolResultMessage("t2", "status: failed", True)], text="also")]
    out = to_anthropic_messages(msgs)
    assert out[1]["content"] == [{"type": "text", "text": "checking"}, {"type": "tool_use", "id": "t1", "name": "sys_disk", "input": {}}]
    assert [b["type"] for b in out[2]["content"]] == ["tool_result", "tool_result", "text"]  # results come first
    assert out[2]["content"][1]["is_error"] is True
    json.dumps(out)  # must be serialisable
    assert to_anthropic_messages([Message("assistant")])[0]["content"][0]["text"]  # never an empty content array


def test_response_parsing_handles_text_and_tool_use():
    resp = NS(content=[NS(type="text", text="Looking."), NS(type="tool_use", id="tu1", name="sys_disk", input={"a": 1}), NS(type="thinking")],
              stop_reason="tool_use", usage=NS(input_tokens=12, output_tokens=7))
    r = parse_response(resp, "m")
    assert r.text == "Looking." and r.tool_calls == [ToolCall("tu1", "sys_disk", {"a": 1})]
    assert (r.stop_reason, r.usage.input_tokens, r.usage.output_tokens, r.model) == ("tool_use", 12, 7, "m")


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class HTTPish(Exception):
    def __init__(self, code, msg="x"):
        super().__init__(msg)
        self.status_code = code


@pytest.mark.parametrize("exc,expected", [
    (APITimeoutError("t"), ("timeout", True)), (TimeoutError(), ("timeout", True)), (APIConnectionError("c"), ("network", True)),
    (HTTPish(401), ("auth", False)), (HTTPish(403), ("auth", False)), (HTTPish(429), ("rate_limit", True)),
    (HTTPish(500), ("server", True)), (HTTPish(529), ("server", True)), (HTTPish(400), ("bad_request", False)), (ValueError("?"), ("unknown", False)),
])
def test_exception_classification(exc, expected):
    assert classify_exception(exc) == expected


class FakeClient:
    def __init__(self, result=None, exc=None):
        self.calls, self.result, self.exc = [], result, exc
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.result


def test_provider_sends_system_tools_and_model_and_parses_the_reply():
    client = FakeClient(NS(content=[NS(type="text", text="hello")], stop_reason="end_turn", usage=NS(input_tokens=1, output_tokens=2)))
    p = AnthropicProvider("k", "claude-sonnet-5-5", client=client)
    out = p.complete(system="SYS", messages=[Message("user", text="hi")], tools=[ToolSpec("t", "d", {"type": "object"})], max_tokens=99)
    sent = client.calls[0]
    assert out.text == "hello" and sent["model"] == "claude-sonnet-5-5" and sent["system"] == "SYS" and sent["max_tokens"] == 99
    assert sent["tools"] == [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    assert "tools" not in AnthropicProvider("k", "m", client=FakeClient(NS(content=[], usage=None))).complete(system="s", messages=[Message("user", text="x")], tools=[]).__dict__


def test_provider_errors_are_classified_and_never_leak_the_key():
    client = FakeClient(exc=HTTPish(401, "invalid x-api-key sk-ant-abcdefghijklmnopqrstu"))
    with pytest.raises(ProviderError) as ei:
        AnthropicProvider("k", "m", client=client).complete(system="s", messages=[Message("user", text="x")], tools=[])
    assert ei.value.kind == "auth" and not ei.value.retryable and "sk-ant-abcdefghijklmnopqrstu" not in str(ei.value)


def test_provider_without_a_key_is_not_configured():
    p = AnthropicProvider(None, "m")
    assert not p.is_configured()
    with pytest.raises(ProviderError) as ei:
        p.complete(system="s", messages=[], tools=[])
    assert ei.value.kind == "not_configured"


@pytest.mark.skipif(os.environ.get("ZANE_NETWORK_TESTS") != "1", reason="set ZANE_NETWORK_TESTS=1 to hit the live API")
def test_live_api_rejects_a_bad_key_with_a_classified_error():
    p = AnthropicProvider("sk-ant-invalid-key-for-zane-test", "claude-sonnet-5-5", timeout_s=20, max_retries=0)
    with pytest.raises(ProviderError) as ei:
        p.complete(system="s", messages=[Message("user", text="hi")], tools=[], max_tokens=5)
    assert ei.value.kind == "auth"


# ---- config ----------------------------------------------------------------------------------
def test_parse_dotenv():
    text = "# c\nA=1\nexport B='two words'\nC=\"q\"\nD=val # trailing\n\nBAD LINE\nE=\n"
    assert parse_dotenv(text) == {"A": "1", "B": "two words", "C": "q", "D": "val", "E": ""}


def load(tmp_path, env=None, dotenv=None, platform="linux", runner=None):
    paths = []
    if dotenv is not None:
        f = tmp_path / ".env"
        f.write_text(dotenv)
        paths = [f]
    return Config.load(env=env or {}, dotenv_paths=paths, runner=runner or FakeRunner(), platform=platform)


def test_real_environment_beats_dotenv_and_dotenv_beats_nothing(tmp_path):
    c = load(tmp_path, env={"ANTHROPIC_API_KEY": "env-key"}, dotenv="ANTHROPIC_API_KEY=file-key\nZANE_MODEL=file-model\n")
    assert (c.api_key, c.api_key_source, c.model) == ("env-key", "environment", "file-model")
    c = load(tmp_path, dotenv="ANTHROPIC_API_KEY=file-key\n")
    assert (c.api_key, c.api_key_source) == ("file-key", ".env")
    c = load(tmp_path, dotenv="ANTHROPIC_API_KEY=\n")  # the .env.example placeholder means 'unset'
    assert (c.api_key, c.api_key_source) == (None, "none")


def test_keychain_is_used_on_macos_only(tmp_path, monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "tester")
    fr = FakeRunner().when(["security", "find-generic-password", "-s", SERVICE, "-a", "tester", "-w"], stdout="kc-key\n")
    c = load(tmp_path, platform="darwin", runner=fr)
    assert (c.api_key, c.api_key_source) == ("kc-key", "keychain") and fr.calls == [["security", "find-generic-password", "-s", SERVICE, "-a", "tester", "-w"]]
    fr2 = FakeRunner().when(["security"], stdout="kc-key\n")
    assert load(tmp_path, platform="linux", runner=fr2).api_key is None and fr2.calls == []
    assert load(tmp_path, env={"ANTHROPIC_API_KEY": "e"}, platform="darwin", runner=fr2).api_key == "e" and fr2.calls == []  # env first, no keychain prompt
    assert keychain_clear(FakeRunner().when(["security", "delete-generic-password"]), "tester")
    assert keychain_get(FakeRunner().when(["security"], returncode=44), "tester") is None


def test_the_api_key_never_appears_in_repr_or_str(tmp_path):
    c = load(tmp_path, env={"ANTHROPIC_API_KEY": "sk-ant-supersecretvalue123456"})
    assert "supersecret" not in repr(c) and "supersecret" not in str(c)


def test_defaults_and_overrides(tmp_path):
    c = load(tmp_path)
    assert c.model == "claude-sonnet-5" and c.fast_path and c.reminders and c.approved_dirs[0].name == "Downloads"
    c = load(tmp_path, env={"ZANE_APPROVED_DIRS": f"~/a{os.pathsep}/tmp/b", "ZANE_FAST_PATH": "0", "ZANE_REMINDERS": "no", "ZANE_MAX_TOKENS": "900",
                            "ZANE_TIMEOUT": "oops", "ZANE_HOME": str(tmp_path / "h"), "ZANE_MODEL": "claude-haiku-5-5"})
    assert [p.name for p in c.approved_dirs] == ["a", "b"] and not c.fast_path and not c.reminders
    assert (c.max_tokens, c.request_timeout_s, c.model, c.data_dir) == (900, 60.0, "claude-haiku-5-5", tmp_path / "h")
    assert c.db_path == tmp_path / "h" / "zane.db"
