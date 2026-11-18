from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import SecretStr

from agent_worker.agent.claude_client import ClaudeAPIError, ClaudeClient
from agent_worker.agent.types import Message, ToolCall
from agent_worker.tools.base import ToolSpec


# ---------- fakes ----------


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _FakeBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    content: list[_FakeBlock]
    stop_reason: str = "end_turn"
    usage: _FakeUsage | None = None


class _FakeMessages:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return self.response


class _FakeAnthropicClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.messages = _FakeMessages(response)


@dataclass
class _StubSettings:
    claude_api_key: SecretStr = SecretStr("test-key")
    claude_model: str = "claude-opus-4-7"
    claude_effort: str = "high"
    claude_max_tokens: int = 4096


def _make_client(
    response: _FakeResponse, *, model: str | None = None
) -> tuple[ClaudeClient, _FakeAnthropicClient]:
    fake = _FakeAnthropicClient(response)
    client = ClaudeClient(_StubSettings(), client=fake, model=model)
    return client, fake


# ---------- request building ----------


def test_request_basic_user_message() -> None:
    client, fake = _make_client(
        _FakeResponse(
            content=[_FakeBlock(type="text", text="hello back")],
            usage=_FakeUsage(input_tokens=5, output_tokens=3),
        )
    )
    client.generate(system_prompt="sys", messages=[Message.user("hello")])
    sent = fake.messages.calls[0]
    assert sent["model"] == "claude-opus-4-7"
    assert sent["max_tokens"] == 4096
    # System prompt is wrapped with cache_control for prompt caching
    assert sent["system"][0]["text"] == "sys"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Adaptive thinking + effort enabled by default for Opus 4.7
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "high"}
    # Messages translated correctly
    assert sent["messages"] == [{"role": "user", "content": "hello"}]


def test_request_with_tools() -> None:
    client, fake = _make_client(
        _FakeResponse(content=[_FakeBlock(type="text", text="ok")])
    )
    spec = ToolSpec(
        name="read_file",
        description="reads a file",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    client.generate(
        system_prompt="sys", messages=[Message.user("hi")], tools=[spec]
    )
    sent = fake.messages.calls[0]
    assert sent["tools"][0]["name"] == "read_file"
    assert sent["tools"][0]["description"] == "reads a file"
    assert sent["tools"][0]["input_schema"]["properties"]["path"]["type"] == "string"


def test_request_assistant_with_tool_calls() -> None:
    client, fake = _make_client(
        _FakeResponse(content=[_FakeBlock(type="text", text="done")])
    )
    msg = Message.assistant(
        content="thinking...",
        tool_calls=[ToolCall(id="toolu_1", name="read_file", arguments={"path": "x"})],
    )
    client.generate(system_prompt="sys", messages=[Message.user("hi"), msg])
    contents = fake.messages.calls[0]["messages"]
    assert contents[1]["role"] == "assistant"
    assert contents[1]["content"][0] == {"type": "text", "text": "thinking..."}
    assert contents[1]["content"][1] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "read_file",
        "input": {"path": "x"},
    }


def test_request_tool_message_uses_tool_result_block() -> None:
    client, fake = _make_client(
        _FakeResponse(content=[_FakeBlock(type="text", text="ok")])
    )
    tool_msg = Message.tool(tool_call_id="toolu_1", content="file contents", name="read_file")
    client.generate(system_prompt="sys", messages=[Message.user("hi"), tool_msg])
    contents = fake.messages.calls[0]["messages"]
    assert contents[1]["role"] == "user"
    assert contents[1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "file contents",
    }


def test_max_tokens_override() -> None:
    client, fake = _make_client(
        _FakeResponse(content=[_FakeBlock(type="text", text="ok")])
    )
    client.generate(system_prompt="s", messages=[Message.user("h")], max_tokens=1000)
    assert fake.messages.calls[0]["max_tokens"] == 1000


def test_thinking_disabled_for_older_models() -> None:
    client, fake = _make_client(
        _FakeResponse(content=[_FakeBlock(type="text", text="ok")]),
        model="claude-haiku-4-5",
    )
    client.generate(system_prompt="s", messages=[Message.user("h")])
    sent = fake.messages.calls[0]
    # haiku-4-5 does not support adaptive thinking or effort
    assert "thinking" not in sent
    assert "output_config" not in sent


def test_thinking_explicitly_disabled() -> None:
    fake = _FakeAnthropicClient(
        _FakeResponse(content=[_FakeBlock(type="text", text="ok")])
    )
    client = ClaudeClient(
        _StubSettings(), client=fake, thinking_enabled=False
    )
    client.generate(system_prompt="s", messages=[Message.user("h")])
    sent = fake.messages.calls[0]
    assert "thinking" not in sent
    # effort still kicks in for Opus 4.7 even without thinking
    assert sent["output_config"] == {"effort": "high"}


# ---------- response parsing ----------


def test_parse_text_response() -> None:
    client, _ = _make_client(
        _FakeResponse(
            content=[_FakeBlock(type="text", text="hello world")],
            stop_reason="end_turn",
            usage=_FakeUsage(input_tokens=12, output_tokens=5),
        )
    )
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text == "hello world"
    assert result.tool_calls == []
    assert result.tokens_used == 17
    assert result.finish_reason == "end_turn"


def test_parse_tool_use_response() -> None:
    client, _ = _make_client(
        _FakeResponse(
            content=[
                _FakeBlock(
                    type="tool_use",
                    id="toolu_abc",
                    name="write_file",
                    input={"path": "x", "content": "y"},
                )
            ],
            stop_reason="tool_use",
            usage=_FakeUsage(input_tokens=10, output_tokens=20),
        )
    )
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "toolu_abc"
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].arguments == {"path": "x", "content": "y"}
    assert result.finish_reason == "tool_use"


def test_parse_mixed_text_and_tool_use() -> None:
    client, _ = _make_client(
        _FakeResponse(
            content=[
                _FakeBlock(type="text", text="let me read it"),
                _FakeBlock(
                    type="tool_use",
                    id="toolu_1",
                    name="read_file",
                    input={"path": "y"},
                ),
            ],
            stop_reason="tool_use",
        )
    )
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text == "let me read it"
    assert result.tool_calls[0].name == "read_file"


def test_parse_thinking_block_is_dropped() -> None:
    """Thinking blocks come back from Anthropic but we don't surface them."""
    client, _ = _make_client(
        _FakeResponse(
            content=[
                _FakeBlock(type="thinking", text="internal reasoning"),
                _FakeBlock(type="text", text="my answer"),
            ],
            stop_reason="end_turn",
        )
    )
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text == "my answer"
    assert "internal reasoning" not in (result.text or "")


def test_unknown_message_role_raises() -> None:
    client, _ = _make_client(_FakeResponse(content=[]))
    with pytest.raises(ValueError, match="unhandled message role"):
        client.generate(
            system_prompt="s", messages=[Message(role="weird", content="x")]
        )


def test_missing_anthropic_sdk_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construct without injecting a client; first .generate() call should fail
    cleanly with ClaudeAPIError when anthropic isn't importable."""
    import sys

    # Hide the real anthropic module from import system
    monkeypatch.setitem(sys.modules, "anthropic", None)
    client = ClaudeClient(_StubSettings())
    with pytest.raises(ClaudeAPIError, match="not installed"):
        client.generate(system_prompt="s", messages=[Message.user("h")])
