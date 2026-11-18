from __future__ import annotations

import time
from dataclasses import dataclass

from agent_worker.agent.client import LLMClient
from agent_worker.agent.event_recorder import (
    EventRecorder,
    NoOpEventRecorder,
    summarize_args,
)
from agent_worker.agent.token_budget import TokenBudget
from agent_worker.agent.types import Message
from agent_worker.tools.base import ToolContext, ToolError, ToolRegistry


class AgentLoopExhausted(Exception):
    def __init__(self, iterations: int) -> None:
        super().__init__(f"agent tool-loop exhausted after {iterations} iterations")
        self.iterations = iterations


@dataclass
class AgentResult:
    text: str
    iterations: int
    messages: list[Message]


class AgentToolLoop:
    """Bounded LLM tool-calling loop.

    Calls the LLM, executes any tool calls it returns, feeds the results back,
    repeats until the LLM returns a final text response (no tool calls) or the
    iteration cap is reached.

    Emits `llm.called` and `tool.invoked` events to the configured EventRecorder
    around each operation. Default recorder is a no-op so standalone use stays
    cost-free.
    """

    MAX_ITERATIONS = 25

    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        budget: TokenBudget,
        *,
        max_iterations: int | None = None,
        recorder: EventRecorder | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.budget = budget
        self.max_iterations = max_iterations or self.MAX_ITERATIONS
        self.recorder: EventRecorder = recorder or NoOpEventRecorder()

    def run(
        self,
        *,
        system_prompt: str,
        user_message: str,
        allowed_tools: list[str],
        ctx: ToolContext,
    ) -> AgentResult:
        allowed = set(allowed_tools)
        for name in allowed:
            self.registry.spec(name)  # raises KeyError if unknown — fail fast

        messages: list[Message] = [Message.user(user_message)]
        tool_specs = [self.registry.spec(n) for n in allowed_tools]

        for iteration in range(1, self.max_iterations + 1):
            self.budget.check()

            t0 = time.perf_counter()
            result = self.client.generate(
                system_prompt=system_prompt,
                messages=messages,
                tools=tool_specs,
            )
            llm_latency_ms = int((time.perf_counter() - t0) * 1000)
            self.budget.add(result.tokens_used)

            self.recorder.emit(
                "llm.called",
                {
                    "iteration": iteration,
                    "tokens_used": result.tokens_used,
                    "finish_reason": result.finish_reason,
                    "has_text": bool(result.text),
                    "num_tool_calls": len(result.tool_calls),
                    "latency_ms": llm_latency_ms,
                },
            )

            if not result.tool_calls:
                return AgentResult(
                    text=result.text or "",
                    iterations=iteration,
                    messages=messages,
                )

            messages.append(
                Message.assistant(content=result.text, tool_calls=result.tool_calls)
            )

            for call in result.tool_calls:
                t0 = time.perf_counter()
                ok = True
                error: str | None = None
                if call.name not in allowed:
                    output = (
                        f"ToolNotAllowed: {call.name!r} is not available in this stage. "
                        f"Allowed tools: {sorted(allowed)}."
                    )
                    ok = False
                    error = "ToolNotAllowed"
                else:
                    try:
                        output = self.registry.invoke(call.name, call.arguments, ctx)
                    except ToolError as e:
                        output = f"ToolError: {e}"
                        ok = False
                        error = "ToolError"
                    except KeyError as e:
                        output = f"ToolError: {e}"
                        ok = False
                        error = "ToolError"
                tool_latency_ms = int((time.perf_counter() - t0) * 1000)

                self.recorder.emit(
                    "tool.invoked",
                    {
                        "iteration": iteration,
                        "tool": call.name,
                        "args": summarize_args(call.arguments),
                        "ok": ok,
                        "error": error,
                        "latency_ms": tool_latency_ms,
                        "output_size": len(output),
                    },
                )
                messages.append(Message.tool(call.id, output, name=call.name))

        raise AgentLoopExhausted(self.max_iterations)
