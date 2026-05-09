from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import SecretStr

from saturn_agent.agent.client import GeminiAPIError, GeminiClient
from saturn_agent.agent.types import Message, ToolCall
from saturn_agent.tools.base import ToolSpec


# ---------- fakes ----------


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict[str, Any]

    @property
    def text(self) -> str:
        return json.dumps(self.payload)

    def json(self) -> dict[str, Any]:
        return self.payload


class _FakeTransport:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response


@dataclass
class _StubSettings:
    gemini_api_url: str = "https://example.test/v1beta"
    gemini_api_key: SecretStr = SecretStr("super-secret")
    gemini_model: str = "gemini-2.5-pro"
    gemini_max_output_tokens: int = 1024
    gemini_temperature: float = 0.1


def _client_with(payload: dict[str, Any], status: int = 200) -> tuple[GeminiClient, _FakeTransport]:
    transport = _FakeTransport(_FakeResponse(status, payload))
    client = GeminiClient(_StubSettings(), transport=transport)
    return client, transport


# ---------- request building ----------


def test_endpoint_url() -> None:
    client, _ = _client_with({"candidates": []})
    assert (
        client._endpoint()
        == "https://example.test/v1beta/models/gemini-2.5-pro:generateContent"
    )


def test_headers_carry_api_key() -> None:
    client, _ = _client_with({"candidates": []})
    headers = client._headers()
    assert headers["x-goog-api-key"] == "super-secret"
    assert headers["Content-Type"] == "application/json"


def test_request_basic_user_message() -> None:
    client, transport = _client_with(
        {"candidates": [{"content": {"parts": [{"text": "hi back"}]}, "finishReason": "STOP"}]}
    )
    client.generate(system_prompt="sys", messages=[Message.user("hello")])
    sent = transport.calls[0]["json"]
    assert sent["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert sent["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert "tools" not in sent
    assert sent["generationConfig"]["maxOutputTokens"] == 1024


def test_request_with_tools() -> None:
    client, transport = _client_with({"candidates": []})
    spec = ToolSpec(
        name="read_file",
        description="reads a file",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    client.generate(
        system_prompt="sys", messages=[Message.user("hi")], tools=[spec]
    )
    sent = transport.calls[0]["json"]
    decls = sent["tools"][0]["functionDeclarations"]
    assert decls[0]["name"] == "read_file"
    assert decls[0]["description"] == "reads a file"
    assert decls[0]["parameters"]["properties"]["path"]["type"] == "string"


def test_request_assistant_with_tool_calls_maps_to_model_role() -> None:
    client, transport = _client_with({"candidates": []})
    msg = Message.assistant(
        content="thinking...",
        tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "x"})],
    )
    client.generate(system_prompt="sys", messages=[Message.user("hi"), msg])
    contents = transport.calls[0]["json"]["contents"]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0] == {"text": "thinking..."}
    assert contents[1]["parts"][1] == {
        "functionCall": {"name": "read_file", "args": {"path": "x"}}
    }


def test_request_tool_message_uses_tool_name_for_function_response() -> None:
    client, transport = _client_with({"candidates": []})
    tool_msg = Message.tool(tool_call_id="c1", content="file contents", name="read_file")
    client.generate(
        system_prompt="sys", messages=[Message.user("hi"), tool_msg]
    )
    contents = transport.calls[0]["json"]["contents"]
    fr = contents[1]["parts"][0]["functionResponse"]
    assert fr["name"] == "read_file"
    assert fr["response"] == {"output": "file contents"}


def test_max_tokens_override() -> None:
    client, transport = _client_with({"candidates": []})
    client.generate(system_prompt="s", messages=[Message.user("h")], max_tokens=42)
    assert transport.calls[0]["json"]["generationConfig"]["maxOutputTokens"] == 42


# ---------- response parsing ----------


def test_parse_text_only_response() -> None:
    client, _ = _client_with(
        {
            "candidates": [
                {"content": {"parts": [{"text": "hello "}, {"text": "world"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {"totalTokenCount": 17},
        }
    )
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text == "hello world"
    assert result.tool_calls == []
    assert result.tokens_used == 17
    assert result.finish_reason == "STOP"


def test_parse_function_call_response() -> None:
    client, _ = _client_with(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "write_file", "args": {"path": "x"}}}
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"totalTokenCount": 5},
        }
    )
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].arguments == {"path": "x"}


def test_parse_no_candidates() -> None:
    client, _ = _client_with({"candidates": []})
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text is None
    assert result.tool_calls == []
    assert result.finish_reason == "no_candidates"


def test_parse_mixed_text_and_function_call() -> None:
    client, _ = _client_with(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "let me read it"},
                            {
                                "functionCall": {
                                    "name": "read_file",
                                    "args": {"path": "y"},
                                }
                            },
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
    )
    result = client.generate(system_prompt="s", messages=[Message.user("h")])
    assert result.text == "let me read it"
    assert result.tool_calls[0].name == "read_file"


# ---------- errors ----------


def test_400_raises_api_error() -> None:
    client, _ = _client_with({"error": "bad request"}, status=400)
    with pytest.raises(GeminiAPIError) as exc_info:
        client.generate(system_prompt="s", messages=[Message.user("h")])
    assert exc_info.value.status_code == 400


def test_500_raises_api_error() -> None:
    client, _ = _client_with({"error": "server"}, status=503)
    with pytest.raises(GeminiAPIError):
        client.generate(system_prompt="s", messages=[Message.user("h")])


def test_unknown_message_role_raises_value_error() -> None:
    client, _ = _client_with({"candidates": []})
    bad = Message(role="unexpected", content="x")
    with pytest.raises(ValueError, match="unhandled message role"):
        client.generate(system_prompt="s", messages=[bad])
