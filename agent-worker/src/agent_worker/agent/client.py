from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Protocol

from agent_worker.agent.types import GenerateResult, Message, ToolCall
from agent_worker.tools.base import ToolSpec


class LLMClient(Protocol):
    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> GenerateResult: ...


class GeminiAPIError(Exception):
    """Raised when the Gemini API returns an error or unparseable response."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Gemini API returned {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body


class _HTTPTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> Any: ...


class GeminiClient:
    """Adapter for Google's Gemini REST API (generativelanguage.googleapis.com).

    Translates this codebase's provider-agnostic Message/ToolSpec into Gemini's
    `contents` + `tools.functionDeclarations` shape, then maps the response
    back to GenerateResult.

    If your internal Gemini exposes a different shape (OpenAI-compatible proxy,
    Vertex AI, custom authentication), swap this class for one that targets
    your endpoint. The Protocol-based LLMClient interface keeps the rest of
    the agent code unchanged.
    """

    def __init__(
        self,
        settings: Any,
        *,
        transport: _HTTPTransport | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.settings = settings
        if transport is not None:
            self._client = transport
            self._owns_client = False
        else:
            import httpx

            self._client = httpx.Client(timeout=timeout_s)
            self._owns_client = True

    def close(self) -> None:
        if self._owns_client:
            self._client.close()  # type: ignore[attr-defined]

    def __enter__(self) -> GeminiClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> GenerateResult:
        body = self._build_request(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens
            or int(getattr(self.settings, "gemini_max_output_tokens", 8192)),
        )
        response = self._client.post(
            self._endpoint(), json=body, headers=self._headers()
        )
        status = getattr(response, "status_code", 0)
        if status >= 400:
            raise GeminiAPIError(status, getattr(response, "text", ""))
        return self._parse_response(response.json())

    # ---------- internals ----------

    def _endpoint(self) -> str:
        base = str(self.settings.gemini_api_url).rstrip("/")
        model = self.settings.gemini_model
        return f"{base}/models/{model}:generateContent"

    def _headers(self) -> dict[str, str]:
        key = self.settings.gemini_api_key
        if hasattr(key, "get_secret_value"):
            key_value = key.get_secret_value()
        else:
            key_value = str(key)
        return {
            "x-goog-api-key": key_value,
            "Content-Type": "application/json",
        }

    def _build_request(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                contents.append(
                    {"role": "user", "parts": [{"text": m.content or ""}]}
                )
            elif m.role == "assistant":
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    parts.append(
                        {"functionCall": {"name": tc.name, "args": tc.arguments}}
                    )
                contents.append({"role": "model", "parts": parts})
            elif m.role == "tool":
                # Gemini wants tool results as functionResponse parts in a user-role
                # message, keyed by function NAME (not tool_call_id).
                fname = m.tool_name or m.tool_call_id or "unknown_tool"
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": fname,
                                    "response": {"output": m.content or ""},
                                }
                            }
                        ],
                    }
                )
            else:
                raise ValueError(f"unhandled message role: {m.role!r}")

        body: dict[str, Any] = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": float(
                    getattr(self.settings, "gemini_temperature", 0.1)
                ),
            },
        }
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters_schema,
                        }
                        for t in tools
                    ]
                }
            ]
        return body

    def _parse_response(self, response: dict[str, Any]) -> GenerateResult:
        usage = response.get("usageMetadata") or {}
        tokens_used = int(usage.get("totalTokenCount") or 0)

        candidates = response.get("candidates") or []
        if not candidates:
            return GenerateResult(
                text=None,
                tool_calls=[],
                tokens_used=tokens_used,
                finish_reason="no_candidates",
            )

        cand = candidates[0]
        finish_reason = str(cand.get("finishReason") or "stop")
        parts = (cand.get("content") or {}).get("parts") or []

        text_segments: list[str] = []
        tool_calls: list[ToolCall] = []
        for i, part in enumerate(parts):
            if "text" in part and part["text"]:
                text_segments.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"] or {}
                tool_calls.append(
                    ToolCall(
                        id=f"call_{i}",
                        name=str(fc.get("name") or ""),
                        arguments=dict(fc.get("args") or {}),
                    )
                )

        return GenerateResult(
            text="".join(text_segments) if text_segments else None,
            tool_calls=tool_calls,
            tokens_used=tokens_used,
            finish_reason=finish_reason,
        )


class FakeLLMClient:
    """Test double that yields scripted GenerateResult values in order.

    Records each call so tests can assert on prompts, tool specs, and message history.
    """

    def __init__(self, responses: Iterable[GenerateResult]) -> None:
        self._responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> GenerateResult:
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of scripted responses")
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": list(messages),
                "tools": list(tools or []),
                "max_tokens": max_tokens,
            }
        )
        return self._responses.popleft()

    @property
    def remaining(self) -> int:
        return len(self._responses)


def load_fake_client_from_script(path: Path) -> FakeLLMClient:
    """Build a FakeLLMClient from a JSON file with scripted responses.

    Schema:

        {
          "responses": [
            {
              "text": "...",                        # optional
              "tool_calls": [                       # optional
                {"id": "...", "name": "...", "arguments": {...}}
              ],
              "tokens_used": 123,                   # optional, defaults to 0
              "finish_reason": "stop"               # optional
            },
            ...
          ]
        }
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("responses"), list):
        raise ValueError(
            f"mock script must be a JSON object with a 'responses' list (got {path})"
        )

    responses: list[GenerateResult] = []
    for i, entry in enumerate(raw["responses"]):
        if not isinstance(entry, dict):
            raise ValueError(f"response {i} must be a JSON object")
        tool_calls_raw = entry.get("tool_calls", []) or []
        tool_calls: list[ToolCall] = []
        for j, tc in enumerate(tool_calls_raw):
            if not isinstance(tc, dict):
                raise ValueError(f"response {i}, tool_call {j} must be a JSON object")
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or f"call_{i}_{j}"),
                    name=str(tc["name"]),
                    arguments=dict(tc.get("arguments") or {}),
                )
            )
        responses.append(
            GenerateResult(
                text=entry.get("text"),
                tool_calls=tool_calls,
                tokens_used=int(entry.get("tokens_used") or 0),
                finish_reason=str(entry.get("finish_reason") or "stop"),
            )
        )
    return FakeLLMClient(responses)
