from __future__ import annotations

from typing import Any

import pytest

from agent_worker.tools.base import (
    Tool,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)


class _Echo(Tool):
    spec = ToolSpec(
        name="echo",
        description="echo back the message",
        parameters_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        return str(arguments.get("msg", ""))


def test_register_and_invoke(tool_ctx: ToolContext) -> None:
    reg = ToolRegistry()
    reg.register(_Echo())
    assert reg.names() == ["echo"]
    assert reg.invoke("echo", {"msg": "hi"}, tool_ctx) == "hi"


def test_register_duplicate_raises() -> None:
    reg = ToolRegistry()
    reg.register(_Echo())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_Echo())


def test_get_unknown_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="unknown tool"):
        reg.get("nope")


def test_resolve_within_workspace_blocks_traversal(tool_ctx: ToolContext) -> None:
    from agent_worker.tools.base import ToolError

    with pytest.raises(ToolError, match="escapes workspace"):
        tool_ctx.resolve_within_workspace("../../../etc/passwd")


def test_resolve_within_workspace_allows_subdir(tool_ctx: ToolContext) -> None:
    resolved = tool_ctx.resolve_within_workspace("repos/foo/bar.scala")
    assert resolved.is_relative_to(tool_ctx.workspace_path.resolve())
