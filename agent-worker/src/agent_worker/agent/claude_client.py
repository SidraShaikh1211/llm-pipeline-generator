"""Adapter for Anthropic Claude via the official `anthropic` SDK.

Implements the same `LLMClient` Protocol as `GeminiClient` and `FakeLLMClient`
so the rest of the agent (tool loop, orchestrator) is provider-agnostic.
"""

from __future__ import annotations

from typing import Any

from agent_worker.agent.types import GenerateResult, Message, ToolCall
from agent_worker.tools.base import ToolSpec


_ADAPTIVE_THINKING_MODELS = ("claude-opus-4-", "claude-sonnet-4-6", "claude-sonnet-4-7")
_EFFORT_MODELS = ("claude-opus-4-", "claude-sonnet-4-6", "claude-sonnet-4-7")


class ClaudeAPIError(Exception):
    """Wrapper around Anthropic SDK errors so callers can catch a single type."""


class ClaudeClient:
    """Translates this codebase's Message / ToolSpec to Anthropic's wire format
    and back. Defaults match the claude-api skill: `claude-opus-4-7`, adaptive
    thinking, effort=high, prompt caching on the system block.

    Construction is lazy: the SDK client is created on first use so that
    importing this module is cheap even when Claude isn't being used.
    """

    def __init__(
        self,
        settings: Any,
        *,
        client: Any | None = None,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        thinking_enabled: bool = True,
    ) -> None:
        self.settings = settings
        self.model = model or getattr(settings, "claude_model", "claude-opus-4-7")
        self.effort = effort or getattr(settings, "claude_effort", "high")
        self.max_tokens = max_tokens or int(
            getattr(settings, "claude_max_tokens", 16000)
        )
        self.thinking_enabled = thinking_enabled
        self._client = client
        self._owns_client = client is None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as e:
            raise ClaudeAPIError(
                "anthropic SDK not installed. Install with: pip install anthropic"
            ) from e
        api_key_setting = getattr(self.settings, "claude_api_key", None)
        api_key: str | None = None
        if api_key_setting is not None:
            api_key = (
                api_key_setting.get_secret_value()
                if hasattr(api_key_setting, "get_secret_value")
                else str(api_key_setting)
            )
        try:
            self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        except anthropic.AuthenticationError as e:
            raise ClaudeAPIError(f"Claude auth failed: {e}") from e
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> ClaudeClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # ---------- generate ----------

    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> GenerateResult:
        client = self._ensure_client()
        kwargs = self._build_request(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens or self.max_tokens,
        )
        try:
            import anthropic
        except ImportError as e:
            raise ClaudeAPIError("anthropic SDK not installed") from e
        try:
            response = client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            raise ClaudeAPIError(f"Claude API error {e.status_code}: {e.message}") from e
        return self._parse_response(response)

    # ---------- internals ----------

    def _build_request(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        anthropic_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                anthropic_messages.append(
                    {"role": "user", "content": m.content or ""}
                )
            elif m.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": blocks})
            elif m.role == "tool":
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content or "",
                            }
                        ],
                    }
                )
            else:
                raise ValueError(f"unhandled message role: {m.role!r}")

        # Cache the system prompt so repeated calls within a stage's tool loop
        # hit the cache (~0.1× input cost vs uncached).
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": anthropic_messages,
        }

        if self.thinking_enabled and self._supports_adaptive_thinking():
            kwargs["thinking"] = {"type": "adaptive"}
        if self._supports_effort():
            kwargs["output_config"] = {"effort": self.effort}

        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters_schema,
                }
                for t in tools
            ]
        return kwargs

    def _supports_adaptive_thinking(self) -> bool:
        return any(self.model.startswith(p) for p in _ADAPTIVE_THINKING_MODELS)

    def _supports_effort(self) -> bool:
        return any(self.model.startswith(p) for p in _EFFORT_MODELS)

    def _parse_response(self, response: Any) -> GenerateResult:
        text_segments: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text = getattr(block, "text", "")
                if text:
                    text_segments.append(text)
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )
            # `thinking` blocks are intentionally dropped — we don't surface
            # internal reasoning to the journal in v1.

        usage = getattr(response, "usage", None)
        tokens_used = 0
        if usage is not None:
            tokens_used = (
                int(getattr(usage, "input_tokens", 0) or 0)
                + int(getattr(usage, "output_tokens", 0) or 0)
            )

        return GenerateResult(
            text="".join(text_segments) if text_segments else None,
            tool_calls=tool_calls,
            tokens_used=tokens_used,
            finish_reason=str(getattr(response, "stop_reason", None) or "stop"),
        )
