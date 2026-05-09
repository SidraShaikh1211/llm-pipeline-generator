from __future__ import annotations

from pathlib import Path

import pytest

from saturn_agent.tools.base import ToolContext, ToolError
from saturn_agent.tools.test_devbox import RunDevboxTestsTool


def test_no_devbox_returns_skip(tool_ctx: ToolContext) -> None:
    out = RunDevboxTestsTool().invoke({"specs": ["FooSparkSpec"]}, tool_ctx)
    assert "no devbox configured" in out


def test_with_devbox_returns_stub_message(workspace: Path) -> None:
    ctx = ToolContext(
        job_id="j",
        workspace_path=workspace,
        target_repo_path=workspace,
        devbox_host="dev42.example.com",
        user_id="u",
    )
    out = RunDevboxTestsTool().invoke({"specs": ["FooSparkSpec"]}, ctx)
    assert "dev42.example.com" in out
    assert "not yet wired" in out


def test_specs_must_be_strings(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="list of strings"):
        RunDevboxTestsTool().invoke({"specs": [123]}, tool_ctx)
