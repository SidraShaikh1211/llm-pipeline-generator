from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_worker.agent.client import FakeLLMClient
from agent_worker.agent.event_recorder import EventRecorder
from agent_worker.agent.token_budget import TokenBudget, TokenBudgetExceeded
from agent_worker.agent.tool_loop import AgentLoopExhausted, AgentToolLoop
from agent_worker.agent.types import GenerateResult, ToolCall
from agent_worker.tools import builtin_registry
from agent_worker.tools.base import Tool, ToolContext, ToolError, ToolRegistry, ToolSpec


class _CapturingRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        self.events.append((event_type, dict(payload or {})))


def _loop_with_responses(responses: list[GenerateResult]) -> tuple[AgentToolLoop, FakeLLMClient]:
    client = FakeLLMClient(responses)
    registry = builtin_registry()
    budget = TokenBudget(max_total=10_000)
    return AgentToolLoop(client, registry, budget), client


def test_returns_when_no_tool_calls(tool_ctx: ToolContext) -> None:
    loop, client = _loop_with_responses(
        [GenerateResult(text="all done", tokens_used=42)]
    )
    result = loop.run(
        system_prompt="sys",
        user_message="hello",
        allowed_tools=["read_file"],
        ctx=tool_ctx,
    )
    assert result.text == "all done"
    assert result.iterations == 1
    assert client.calls[0]["system_prompt"] == "sys"


def test_invokes_tool_then_returns(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    (workspace / "f.txt").write_text("hello world\n")
    loop, _client = _loop_with_responses(
        [
            GenerateResult(
                text=None,
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "f.txt"})],
                tokens_used=10,
            ),
            GenerateResult(text="finished", tokens_used=5),
        ]
    )
    result = loop.run(
        system_prompt="sys",
        user_message="read it",
        allowed_tools=["read_file"],
        ctx=tool_ctx,
    )
    assert result.text == "finished"
    # tool message should have been appended back
    assert any(m.role == "tool" and "hello world" in (m.content or "") for m in result.messages)


def test_tool_outside_allowed_set_returns_text_error(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    (workspace / "f.txt").write_text("x")
    loop, _client = _loop_with_responses(
        [
            GenerateResult(
                text=None,
                tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "f.txt", "content": "x"})],
                tokens_used=1,
            ),
            GenerateResult(text="ok", tokens_used=1),
        ]
    )
    result = loop.run(
        system_prompt="sys",
        user_message="hi",
        allowed_tools=["read_file"],  # write_file NOT allowed
        ctx=tool_ctx,
    )
    tool_msg = next(m for m in result.messages if m.role == "tool")
    assert "ToolNotAllowed" in (tool_msg.content or "")
    assert "write_file" in (tool_msg.content or "")
    # workspace must be unchanged
    assert (workspace / "f.txt").read_text() == "x"


def test_tool_error_surfaces_to_llm(tool_ctx: ToolContext) -> None:
    loop, _client = _loop_with_responses(
        [
            GenerateResult(
                text=None,
                tool_calls=[
                    ToolCall(id="c1", name="read_file", arguments={"path": "missing.txt"})
                ],
                tokens_used=1,
            ),
            GenerateResult(text="ok", tokens_used=1),
        ]
    )
    result = loop.run(
        system_prompt="sys",
        user_message="hi",
        allowed_tools=["read_file"],
        ctx=tool_ctx,
    )
    tool_msg = next(m for m in result.messages if m.role == "tool")
    assert "ToolError" in (tool_msg.content or "")


def test_unknown_tool_in_allowed_list_raises_fast() -> None:
    loop, _ = _loop_with_responses([GenerateResult(text="x", tokens_used=1)])
    ctx = ToolContext(
        job_id="j",
        workspace_path=Path("/tmp"),
        target_repo_path=Path("/tmp"),
        user_id="u",
    )
    with pytest.raises(KeyError, match="unknown tool"):
        loop.run(
            system_prompt="sys",
            user_message="hi",
            allowed_tools=["nonexistent_tool"],
            ctx=ctx,
        )


def test_iteration_cap_raises(tool_ctx: ToolContext) -> None:
    # Always returns a tool call → loop never terminates → cap hits.
    responses = [
        GenerateResult(
            text=None,
            tool_calls=[ToolCall(id=f"c{i}", name="read_file", arguments={"path": "x.txt"})],
            tokens_used=1,
        )
        for i in range(50)
    ]
    client = FakeLLMClient(responses)
    loop = AgentToolLoop(
        client, builtin_registry(), TokenBudget(max_total=10_000), max_iterations=3
    )
    with pytest.raises(AgentLoopExhausted):
        loop.run(
            system_prompt="sys",
            user_message="hi",
            allowed_tools=["read_file"],
            ctx=tool_ctx,
        )


def test_token_budget_blows_up_loop(tool_ctx: ToolContext) -> None:
    client = FakeLLMClient(
        [GenerateResult(text="ok", tokens_used=100)]
    )
    budget = TokenBudget(max_total=10)
    budget.add(15)  # already over
    loop = AgentToolLoop(client, builtin_registry(), budget)
    with pytest.raises(TokenBudgetExceeded):
        loop.run(
            system_prompt="sys",
            user_message="hi",
            allowed_tools=["read_file"],
            ctx=tool_ctx,
        )


# ---------- recorder instrumentation ----------


def test_recorder_emits_llm_called_per_iteration(tool_ctx: ToolContext) -> None:
    rec = _CapturingRecorder()
    client = FakeLLMClient(
        [GenerateResult(text="all done", tokens_used=42, finish_reason="stop")]
    )
    loop = AgentToolLoop(
        client, builtin_registry(), TokenBudget(max_total=10_000), recorder=rec
    )
    loop.run(
        system_prompt="sys",
        user_message="hi",
        allowed_tools=["read_file"],
        ctx=tool_ctx,
    )
    llm_events = [e for e in rec.events if e[0] == "llm.called"]
    assert len(llm_events) == 1
    payload = llm_events[0][1]
    assert payload["iteration"] == 1
    assert payload["tokens_used"] == 42
    assert payload["finish_reason"] == "stop"
    assert payload["has_text"] is True
    assert payload["num_tool_calls"] == 0
    assert "latency_ms" in payload


def test_recorder_emits_tool_invoked_with_args_summary(
    tool_ctx: ToolContext, workspace
) -> None:
    (workspace / "f.txt").write_text("contents")
    rec = _CapturingRecorder()
    client = FakeLLMClient(
        [
            GenerateResult(
                text=None,
                tool_calls=[
                    ToolCall(id="c1", name="read_file", arguments={"path": "f.txt"})
                ],
                tokens_used=5,
            ),
            GenerateResult(text="ok", tokens_used=2),
        ]
    )
    loop = AgentToolLoop(
        client, builtin_registry(), TokenBudget(max_total=10_000), recorder=rec
    )
    loop.run(
        system_prompt="sys",
        user_message="hi",
        allowed_tools=["read_file"],
        ctx=tool_ctx,
    )
    tool_events = [e for e in rec.events if e[0] == "tool.invoked"]
    assert len(tool_events) == 1
    payload = tool_events[0][1]
    assert payload["tool"] == "read_file"
    assert payload["args"] == {"path": "f.txt"}
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["output_size"] > 0


def test_recorder_marks_tool_error(tool_ctx: ToolContext) -> None:
    rec = _CapturingRecorder()
    client = FakeLLMClient(
        [
            GenerateResult(
                text=None,
                tool_calls=[
                    ToolCall(id="c1", name="read_file", arguments={"path": "missing.txt"})
                ],
                tokens_used=1,
            ),
            GenerateResult(text="done", tokens_used=1),
        ]
    )
    loop = AgentToolLoop(
        client, builtin_registry(), TokenBudget(max_total=10_000), recorder=rec
    )
    loop.run(
        system_prompt="sys",
        user_message="hi",
        allowed_tools=["read_file"],
        ctx=tool_ctx,
    )
    tool_event = next(e for e in rec.events if e[0] == "tool.invoked")
    assert tool_event[1]["ok"] is False
    assert tool_event[1]["error"] == "ToolError"


def test_recorder_marks_tool_not_allowed(tool_ctx: ToolContext, workspace) -> None:
    (workspace / "f.txt").write_text("x")
    rec = _CapturingRecorder()
    client = FakeLLMClient(
        [
            GenerateResult(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "f.txt", "content": "y"},
                    )
                ],
                tokens_used=1,
            ),
            GenerateResult(text="done", tokens_used=1),
        ]
    )
    loop = AgentToolLoop(
        client, builtin_registry(), TokenBudget(max_total=10_000), recorder=rec
    )
    loop.run(
        system_prompt="sys",
        user_message="hi",
        allowed_tools=["read_file"],
        ctx=tool_ctx,
    )
    tool_event = next(e for e in rec.events if e[0] == "tool.invoked")
    assert tool_event[1]["ok"] is False
    assert tool_event[1]["error"] == "ToolNotAllowed"


def test_recorder_truncates_large_args(tool_ctx: ToolContext, workspace) -> None:
    rec = _CapturingRecorder()
    big = "X" * 500
    client = FakeLLMClient(
        [
            GenerateResult(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "out.txt", "content": big},
                    )
                ],
                tokens_used=1,
            ),
            GenerateResult(text="done", tokens_used=1),
        ]
    )
    loop = AgentToolLoop(
        client, builtin_registry(), TokenBudget(max_total=10_000), recorder=rec
    )
    loop.run(
        system_prompt="sys",
        user_message="hi",
        allowed_tools=["write_file"],
        ctx=tool_ctx,
    )
    tool_event = next(e for e in rec.events if e[0] == "tool.invoked")
    # The recorded args summary must not contain the full content.
    assert big not in tool_event[1]["args"]["content"]
    assert "500 chars" in tool_event[1]["args"]["content"]
