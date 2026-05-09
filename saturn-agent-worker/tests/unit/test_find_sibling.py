from __future__ import annotations

from pathlib import Path

import pytest

from saturn_agent.tools.base import ToolContext, ToolError
from saturn_agent.tools.repo_nav import FindSiblingTool


def _seed_pipelines(workspace: Path) -> Path:
    base = workspace / "repos" / "saturn"
    for name in ("foo_reports", "foo_persist", "bar_reports", "totally_different"):
        (base / name).mkdir(parents=True)
    return base


def test_find_sibling_ranks_by_similarity(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed_pipelines(workspace)
    out = FindSiblingTool().invoke(
        {"pipeline_name": "foo_repots", "search_root": "repos/saturn"}, tool_ctx
    )
    lines = [ln for ln in out.splitlines() if "\t" in ln]
    # Top-ranked should be the typo-distant 'foo_reports'
    assert lines[0].endswith("foo_reports")


def test_find_sibling_excludes_self(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed_pipelines(workspace)
    out = FindSiblingTool().invoke(
        {"pipeline_name": "foo_reports", "search_root": "repos/saturn"}, tool_ctx
    )
    assert "foo_reports" not in [
        ln.split("\t")[-1] for ln in out.splitlines() if "\t" in ln
    ]


def test_find_sibling_top_k(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed_pipelines(workspace)
    out = FindSiblingTool().invoke(
        {"pipeline_name": "foo_reports", "search_root": "repos/saturn", "top_k": 2},
        tool_ctx,
    )
    assert len([ln for ln in out.splitlines() if "\t" in ln]) == 2


def test_find_sibling_no_candidates(tool_ctx: ToolContext, workspace: Path) -> None:
    base = workspace / "repos" / "empty_saturn"
    base.mkdir(parents=True)
    out = FindSiblingTool().invoke(
        {"pipeline_name": "anything", "search_root": "repos/empty_saturn"}, tool_ctx
    )
    assert "no sibling pipelines" in out


def test_find_sibling_search_root_not_dir(tool_ctx: ToolContext, workspace: Path) -> None:
    (workspace / "repos" / "afile").write_text("x")
    with pytest.raises(ToolError, match="not a directory"):
        FindSiblingTool().invoke(
            {"pipeline_name": "x", "search_root": "repos/afile"}, tool_ctx
        )


def test_find_sibling_traversal_blocked(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes workspace"):
        FindSiblingTool().invoke(
            {"pipeline_name": "x", "search_root": "../.."}, tool_ctx
        )


def test_find_sibling_requires_args(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="non-empty 'pipeline_name'"):
        FindSiblingTool().invoke({"search_root": "repos"}, tool_ctx)
    with pytest.raises(ToolError, match="non-empty 'search_root'"):
        FindSiblingTool().invoke({"pipeline_name": "x"}, tool_ctx)
