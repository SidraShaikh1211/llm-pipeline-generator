from __future__ import annotations

import csv
import io
from typing import Any

from saturn_agent.tools.base import Tool, ToolContext, ToolError, ToolSpec


MAX_WRITE_BYTES = 512 * 1024  # 512 KB; generated Scala files should not approach this


class WriteFileTool(Tool):
    spec = ToolSpec(
        name="write_file",
        description=(
            "Write a file inside the workspace. Path is relative to the workspace root; "
            "parent directories are created as needed. Existing files are overwritten "
            "by default."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace root"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean", "default": True},
            },
            "required": ["path", "content"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        path = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path, str) or not path:
            raise ToolError("write_file requires a non-empty 'path'")
        if not isinstance(content, str):
            raise ToolError("write_file requires a string 'content'")
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolError(
                f"content too large: {len(content.encode('utf-8'))} bytes > {MAX_WRITE_BYTES}"
            )
        overwrite = bool(arguments.get("overwrite", True))

        target = ctx.resolve_within_workspace(path)
        if target.exists():
            if not overwrite:
                raise ToolError(f"file exists and overwrite=False: {path}")
            if not target.is_file():
                raise ToolError(f"path is not a regular file: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {path} ({len(content)} chars, {target.stat().st_size} bytes)"


class ScaffoldTestFixtureTool(Tool):
    spec = ToolSpec(
        name="scaffold_test_fixture",
        description=(
            "Create a CSV fixture file under the workspace, typically below "
            "src/test/resources. Header is required; rows are optional."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to workspace root, e.g., 'src/test/resources/foo/input.csv'",
                },
                "header": {"type": "array", "items": {"type": "string"}},
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "default": [],
                },
                "overwrite": {"type": "boolean", "default": True},
            },
            "required": ["path", "header"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        path = arguments.get("path")
        header = arguments.get("header")
        rows = arguments.get("rows", []) or []
        overwrite = bool(arguments.get("overwrite", True))

        if not isinstance(path, str) or not path:
            raise ToolError("scaffold_test_fixture requires a non-empty 'path'")
        if not isinstance(header, list) or not header:
            raise ToolError("scaffold_test_fixture requires a non-empty 'header' list of strings")
        if any(not isinstance(c, str) for c in header):
            raise ToolError("'header' entries must be strings")
        if not isinstance(rows, list):
            raise ToolError("'rows' must be a list of lists of strings")
        for row in rows:
            if not isinstance(row, list) or any(not isinstance(c, str) for c in row):
                raise ToolError("each row must be a list of strings")
            if len(row) != len(header):
                raise ToolError(
                    f"row width {len(row)} does not match header width {len(header)}"
                )

        target = ctx.resolve_within_workspace(path)
        if target.exists() and not overwrite:
            raise ToolError(f"file exists and overwrite=False: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
        target.write_text(buf.getvalue(), encoding="utf-8")
        return f"scaffolded {path} ({len(rows)} rows, {len(header)} columns)"
