from __future__ import annotations

from pathlib import Path

import pytest

from agent_worker.tools.base import ToolContext, ToolError
from agent_worker.tools.repo_nav import GrepTool


def _seed(workspace: Path) -> None:
    (workspace / "repos" / "a.scala").write_text(
        "import pipelines.framework.Reference\n"
        "class Foo {\n"
        "  val x = 1\n"
        "}\n"
    )
    (workspace / "repos" / "b.scala").write_text(
        "// no match here\n"
        "class Bar extends Foo {}\n"
    )
    (workspace / "repos" / "notes.md").write_text("Foo appears here too\n")
    sub = workspace / "repos" / "sub"
    sub.mkdir()
    (sub / "c.scala").write_text("object Foo extends App {}\n")


def test_grep_finds_matches_across_files(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed(workspace)
    out = GrepTool().invoke({"pattern": "Foo"}, tool_ctx)
    assert "repos/a.scala:2:" in out
    assert "repos/b.scala:2:" in out
    assert "repos/notes.md:1:" in out
    assert "repos/sub/c.scala:1:" in out


def test_grep_with_glob_filters_files(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed(workspace)
    out = GrepTool().invoke({"pattern": "Foo", "glob": "*.scala"}, tool_ctx)
    assert ".scala:" in out
    assert "notes.md" not in out


def test_grep_no_matches_message(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed(workspace)
    out = GrepTool().invoke({"pattern": "Zzzzz"}, tool_ctx)
    assert "no matches" in out


def test_grep_invalid_regex(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed(workspace)
    with pytest.raises(ToolError, match="invalid regex"):
        GrepTool().invoke({"pattern": "("}, tool_ctx)


def test_grep_ignore_case(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed(workspace)
    out = GrepTool().invoke({"pattern": "FOO", "ignore_case": True}, tool_ctx)
    assert "repos/a.scala" in out


def test_grep_max_results_truncates(tool_ctx: ToolContext, workspace: Path) -> None:
    big = workspace / "repos" / "lots.scala"
    big.write_text("\n".join(["match"] * 50))
    out = GrepTool().invoke({"pattern": "match", "max_results": 5}, tool_ctx)
    assert out.count("\n") <= 6  # 5 results + truncation suffix
    assert "truncated" in out


def test_grep_skips_binary(tool_ctx: ToolContext, workspace: Path) -> None:
    _seed(workspace)
    (workspace / "repos" / "blob.bin").write_bytes(b"Foo\x00\x01\x02match")
    out = GrepTool().invoke({"pattern": "Foo|match"}, tool_ctx)
    assert "blob.bin" not in out


def test_grep_blocks_traversal(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="escapes workspace"):
        GrepTool().invoke({"pattern": "x", "path": "../../etc"}, tool_ctx)


def test_grep_requires_pattern(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="non-empty 'pattern'"):
        GrepTool().invoke({}, tool_ctx)
