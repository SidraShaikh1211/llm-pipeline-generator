from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturn_agent.tools.base import ToolContext, ToolError
from saturn_agent.tools.codebase import (
    ReadConventionsTool,
    ReadReferenceTool,
    ReadSkillTool,
)


# ---------- read_skill ----------


def test_read_skill_returns_skill_md(tool_ctx: ToolContext) -> None:
    out = ReadSkillTool().invoke({"name": "saturn-pipeline-builder"}, tool_ctx)
    assert "saturn-pipeline-builder" in out


def test_read_skill_unknown_name(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="skill not found"):
        ReadSkillTool().invoke({"name": "does-not-exist"}, tool_ctx)


def test_read_skill_blocks_traversal(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes skills_root"):
        ReadSkillTool().invoke({"name": "../../etc"}, tool_ctx)


def test_read_skill_requires_skills_root(workspace: Path) -> None:
    ctx = ToolContext(
        job_id="j",
        workspace_path=workspace,
        target_repo_path=workspace,
        user_id="u",
        skills_root=None,
    )
    with pytest.raises(ToolError, match="skills_root is not configured"):
        ReadSkillTool().invoke({"name": "x"}, ctx)


def test_read_skill_requires_name(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="non-empty 'name'"):
        ReadSkillTool().invoke({}, tool_ctx)


# ---------- read_reference ----------


def test_read_reference_with_md_suffix(tool_ctx: ToolContext) -> None:
    out = ReadReferenceTool().invoke({"name": "partitioning_guide.md"}, tool_ctx)
    assert "Volume tiers" in out


def test_read_reference_without_md_suffix(tool_ctx: ToolContext) -> None:
    out = ReadReferenceTool().invoke({"name": "partitioning_guide"}, tool_ctx)
    assert "Volume tiers" in out


def test_read_reference_unknown(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="reference not found"):
        ReadReferenceTool().invoke({"name": "nope"}, tool_ctx)


def test_read_reference_blocks_traversal(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes references_root"):
        ReadReferenceTool().invoke({"name": "../../etc/passwd"}, tool_ctx)


def test_read_reference_requires_root(workspace: Path) -> None:
    ctx = ToolContext(
        job_id="j",
        workspace_path=workspace,
        target_repo_path=workspace,
        user_id="u",
        references_root=None,
    )
    with pytest.raises(ToolError, match="references_root is not configured"):
        ReadReferenceTool().invoke({"name": "x"}, ctx)


# ---------- read_conventions ----------


def _ctx_with_conventions(
    base: ToolContext, conventions_path: Path
) -> ToolContext:
    return base.model_copy(update={"conventions_path": conventions_path})


def test_read_conventions_full(
    tool_ctx: ToolContext, conventions_path: Path
) -> None:
    ctx = _ctx_with_conventions(tool_ctx, conventions_path)
    out = ReadConventionsTool().invoke({}, ctx)
    data = json.loads(out)
    assert data["repo_name"] == "saturn-pipelines-reports"
    assert {p["pipeline_name"] for p in data["pipelines"]} == {
        "foo_reports",
        "bar_reports",
    }


def test_read_conventions_filtered(
    tool_ctx: ToolContext, conventions_path: Path
) -> None:
    ctx = _ctx_with_conventions(tool_ctx, conventions_path)
    out = ReadConventionsTool().invoke({"pipeline_domain": "foo_reports"}, ctx)
    data = json.loads(out)
    assert len(data["pipelines"]) == 1
    assert data["pipelines"][0]["pipeline_name"] == "foo_reports"


def test_read_conventions_unknown_pipeline(
    tool_ctx: ToolContext, conventions_path: Path
) -> None:
    ctx = _ctx_with_conventions(tool_ctx, conventions_path)
    with pytest.raises(ToolError, match="no pipeline named 'baz'"):
        ReadConventionsTool().invoke({"pipeline_domain": "baz"}, ctx)


def test_read_conventions_missing_file(tool_ctx: ToolContext, tmp_path: Path) -> None:
    ctx = _ctx_with_conventions(tool_ctx, tmp_path / "absent.json")
    with pytest.raises(ToolError, match="conventions file not found"):
        ReadConventionsTool().invoke({}, ctx)


def test_read_conventions_invalid_json(tool_ctx: ToolContext, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    ctx = _ctx_with_conventions(tool_ctx, bad)
    with pytest.raises(ToolError, match="not valid JSON"):
        ReadConventionsTool().invoke({}, ctx)


def test_read_conventions_requires_path(workspace: Path) -> None:
    ctx = ToolContext(
        job_id="j",
        workspace_path=workspace,
        target_repo_path=workspace,
        user_id="u",
        conventions_path=None,
    )
    with pytest.raises(ToolError, match="conventions_path is not configured"):
        ReadConventionsTool().invoke({}, ctx)
