from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """Provider-agnostic chat message; LLM adapters serialize to their wire format."""

    role: str  # "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # set on tool messages
    tool_name: str | None = None  # name of the tool that produced the result

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls, content: str | None = None, tool_calls: list[ToolCall] | None = None
    ) -> Message:
        return cls(role="assistant", content=content, tool_calls=list(tool_calls or []))

    @classmethod
    def tool(
        cls, tool_call_id: str, content: str, *, name: str | None = None
    ) -> Message:
        return cls(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            tool_name=name,
        )


@dataclass
class GenerateResult:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_used: int = 0
    finish_reason: str = "stop"
