from __future__ import annotations

from pathlib import Path

import pytest

from agent_worker.tools.base import ToolContext, ToolError
from agent_worker.tools.emit import ScaffoldTestFixtureTool, WriteFileTool


# ---------- write_file ----------


def test_write_file_creates_parent_dirs(tool_ctx: ToolContext, workspace: Path) -> None:
    out = WriteFileTool().invoke(
        {"path": "src/main/scala/pipelines/foo/FooNode.scala", "content": "class FooNode {}\n"},
        tool_ctx,
    )
    assert "wrote" in out
    written = workspace / "src" / "main" / "scala" / "pipelines" / "foo" / "FooNode.scala"
    assert written.read_text() == "class FooNode {}\n"


def test_write_file_overwrites_by_default(tool_ctx: ToolContext, workspace: Path) -> None:
    (workspace / "f.txt").write_text("old")
    WriteFileTool().invoke({"path": "f.txt", "content": "new"}, tool_ctx)
    assert (workspace / "f.txt").read_text() == "new"


def test_write_file_overwrite_false_raises(tool_ctx: ToolContext, workspace: Path) -> None:
    (workspace / "f.txt").write_text("old")
    with pytest.raises(ToolError, match="overwrite=False"):
        WriteFileTool().invoke(
            {"path": "f.txt", "content": "new", "overwrite": False}, tool_ctx
        )


def test_write_file_blocks_traversal(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes workspace"):
        WriteFileTool().invoke(
            {"path": "../../tmp/evil.txt", "content": "x"}, tool_ctx
        )


def test_write_file_rejects_existing_dir(tool_ctx: ToolContext, workspace: Path) -> None:
    (workspace / "dir").mkdir()
    with pytest.raises(ToolError, match="not a regular file"):
        WriteFileTool().invoke({"path": "dir", "content": "x"}, tool_ctx)


def test_write_file_requires_string_content(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="string 'content'"):
        WriteFileTool().invoke({"path": "f.txt", "content": 42}, tool_ctx)


def test_write_file_too_large(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="too large"):
        WriteFileTool().invoke(
            {"path": "big.txt", "content": "x" * (512 * 1024 + 1)}, tool_ctx
        )


# ---------- scaffold_test_fixture ----------


def test_scaffold_writes_csv(tool_ctx: ToolContext, workspace: Path) -> None:
    out = ScaffoldTestFixtureTool().invoke(
        {
            "path": "src/test/resources/foo/input.csv",
            "header": ["party_id", "name"],
            "rows": [["1", "alpha"], ["2", "beta"]],
        },
        tool_ctx,
    )
    assert "2 rows" in out
    written = workspace / "src" / "test" / "resources" / "foo" / "input.csv"
    assert (
        written.read_text()
        == "party_id,name\n1,alpha\n2,beta\n"
    )


def test_scaffold_with_header_only(tool_ctx: ToolContext, workspace: Path) -> None:
    ScaffoldTestFixtureTool().invoke(
        {"path": "f.csv", "header": ["a", "b"]}, tool_ctx
    )
    assert (workspace / "f.csv").read_text() == "a,b\n"


def test_scaffold_quotes_commas(tool_ctx: ToolContext, workspace: Path) -> None:
    ScaffoldTestFixtureTool().invoke(
        {
            "path": "f.csv",
            "header": ["col"],
            "rows": [["has,comma"]],
        },
        tool_ctx,
    )
    text = (workspace / "f.csv").read_text()
    assert '"has,comma"' in text


def test_scaffold_row_width_mismatch(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="row width"):
        ScaffoldTestFixtureTool().invoke(
            {"path": "f.csv", "header": ["a", "b"], "rows": [["only_one"]]}, tool_ctx
        )


def test_scaffold_requires_header(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="non-empty 'header'"):
        ScaffoldTestFixtureTool().invoke({"path": "f.csv", "header": []}, tool_ctx)


def test_scaffold_blocks_traversal(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes workspace"):
        ScaffoldTestFixtureTool().invoke(
            {"path": "../../etc/x.csv", "header": ["a"]}, tool_ctx
        )
