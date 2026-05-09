from __future__ import annotations

from pathlib import Path

import pytest

from saturn_agent.tools.base import ToolContext, ToolError
from saturn_agent.tools.repo_nav import ListDirTool, ReadFileTool


def test_read_file_returns_numbered_lines(tool_ctx: ToolContext, workspace: Path) -> None:
    f = workspace / "repos" / "Foo.scala"
    f.write_text("alpha\nbeta\ngamma\n")
    out = ReadFileTool().invoke({"path": "repos/Foo.scala"}, tool_ctx)
    assert "repos/Foo.scala" in out
    assert "lines 1-3 of 3" in out
    assert "1\talpha" in out
    assert "3\tgamma" in out


def test_read_file_range(tool_ctx: ToolContext, workspace: Path) -> None:
    (workspace / "f.txt").write_text("a\nb\nc\nd\ne\n")
    out = ReadFileTool().invoke(
        {"path": "f.txt", "start_line": 2, "end_line": 4}, tool_ctx
    )
    assert "lines 2-4 of 5" in out
    assert "2\tb" in out
    assert "4\td" in out
    assert "1\ta" not in out
    assert "5\te" not in out


def test_read_file_not_found(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="file not found"):
        ReadFileTool().invoke({"path": "nope.txt"}, tool_ctx)


def test_read_file_path_traversal_blocked(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes workspace"):
        ReadFileTool().invoke({"path": "../../../etc/passwd"}, tool_ctx)


def test_read_file_rejects_dir(tool_ctx: ToolContext, workspace: Path) -> None:
    with pytest.raises(ToolError, match="not a file"):
        ReadFileTool().invoke({"path": "repos"}, tool_ctx)


def test_read_file_too_large(tool_ctx: ToolContext, workspace: Path) -> None:
    big = workspace / "big.txt"
    big.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(ToolError, match="file too large"):
        ReadFileTool().invoke({"path": "big.txt"}, tool_ctx)


def test_read_file_requires_path(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="non-empty 'path'"):
        ReadFileTool().invoke({}, tool_ctx)


def test_list_dir_workspace_root(tool_ctx: ToolContext) -> None:
    out = ListDirTool().invoke({"path": "."}, tool_ctx)
    assert "dir\trepos" in out
    assert "dir\toutputs" in out


def test_list_dir_default_path(tool_ctx: ToolContext) -> None:
    out = ListDirTool().invoke({}, tool_ctx)
    assert "dir\trepos" in out


def test_list_dir_classifies_files_and_dirs(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    (workspace / "a.txt").write_text("x")
    (workspace / "sub").mkdir()
    out = ListDirTool().invoke({"path": "."}, tool_ctx)
    assert "file\ta.txt" in out
    assert "dir\tsub" in out


def test_list_dir_classifies_symlink(tool_ctx: ToolContext, workspace: Path) -> None:
    target = workspace / "real.txt"
    target.write_text("x")
    (workspace / "link.txt").symlink_to(target)
    out = ListDirTool().invoke({"path": "."}, tool_ctx)
    assert "link\tlink.txt" in out


def test_list_dir_empty(tool_ctx: ToolContext, workspace: Path) -> None:
    (workspace / "empty").mkdir()
    out = ListDirTool().invoke({"path": "empty"}, tool_ctx)
    assert "empty directory" in out


def test_list_dir_not_found(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="path not found"):
        ListDirTool().invoke({"path": "nope"}, tool_ctx)


def test_list_dir_rejects_file(tool_ctx: ToolContext, workspace: Path) -> None:
    (workspace / "a.txt").write_text("x")
    with pytest.raises(ToolError, match="not a directory"):
        ListDirTool().invoke({"path": "a.txt"}, tool_ctx)


def test_list_dir_blocks_traversal(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes workspace"):
        ListDirTool().invoke({"path": "../../"}, tool_ctx)
