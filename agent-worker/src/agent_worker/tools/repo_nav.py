from __future__ import annotations

import difflib
import fnmatch
import re
from pathlib import Path
from typing import Any, Iterator

from agent_worker.tools.base import Tool, ToolContext, ToolError, ToolSpec


MAX_READ_BYTES = 256 * 1024  # 256 KB; agent rarely needs more, and it caps prompt cost
MAX_GREP_FILE_BYTES = 2 * 1024 * 1024  # skip files larger than this when grepping
GREP_DEFAULT_MAX_RESULTS = 200


class ReadFileTool(Tool):
    spec = ToolSpec(
        name="read_file",
        description=(
            "Read a file from the workspace. Returns content with line numbers. "
            "Paths are relative to the workspace root."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace root"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Inclusive. Omit to read to EOF.",
                },
            },
            "required": ["path"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        path = arguments.get("path")
        if not isinstance(path, str) or not path:
            raise ToolError("read_file requires a non-empty 'path'")

        target = ctx.resolve_within_workspace(path)
        if not target.exists():
            raise ToolError(f"file not found: {path}")
        if not target.is_file():
            raise ToolError(f"not a file: {path}")
        if target.stat().st_size > MAX_READ_BYTES:
            raise ToolError(
                f"file too large: {target.stat().st_size} bytes > {MAX_READ_BYTES}"
            )

        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        start = max(int(arguments.get("start_line", 1)), 1)
        end_arg = arguments.get("end_line")
        end = int(end_arg) if end_arg is not None else len(lines)
        if start > len(lines):
            return f"{path}: empty range (file has {len(lines)} lines)"
        end = min(end, len(lines))

        width = len(str(end))
        body = "\n".join(
            f"{i:>{width}}\t{lines[i - 1]}" for i in range(start, end + 1)
        )
        header = f"{path} (lines {start}-{end} of {len(lines)})"
        return f"{header}\n{body}"


class ListDirTool(Tool):
    spec = ToolSpec(
        name="list_dir",
        description=(
            "List directory contents. Returns one entry per line, format: "
            "'<type>\\t<name>' where type is 'dir', 'file', or 'link'."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to workspace root. '' or '.' for the workspace root.",
                    "default": ".",
                },
            },
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        path = arguments.get("path", ".") or "."
        if not isinstance(path, str):
            raise ToolError("list_dir 'path' must be a string")

        target = ctx.resolve_within_workspace(path)
        if not target.exists():
            raise ToolError(f"path not found: {path}")
        if not target.is_dir():
            raise ToolError(f"not a directory: {path}")

        entries: list[str] = []
        for child in sorted(target.iterdir()):
            if child.is_symlink():
                kind = "link"
            elif child.is_dir():
                kind = "dir"
            elif child.is_file():
                kind = "file"
            else:
                kind = "other"
            entries.append(f"{kind}\t{child.name}")

        if not entries:
            return f"{path}: (empty directory)"
        return f"{path}:\n" + "\n".join(entries)


class GrepTool(Tool):
    spec = ToolSpec(
        name="grep",
        description=(
            "Search for a Python regex across files in the workspace. "
            "Returns lines in 'path:lineno:match' form, capped at max_results."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex"},
                "path": {
                    "type": "string",
                    "description": "Subdir to search; default workspace root",
                    "default": ".",
                },
                "glob": {
                    "type": "string",
                    "description": "Filename glob (e.g., '*.scala'); applied to basename",
                    "default": "*",
                },
                "ignore_case": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": GREP_DEFAULT_MAX_RESULTS},
            },
            "required": ["pattern"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolError("grep requires a non-empty 'pattern'")

        path = arguments.get("path", ".") or "."
        glob = arguments.get("glob", "*") or "*"
        ignore_case = bool(arguments.get("ignore_case", False))
        try:
            max_results = int(arguments.get("max_results", GREP_DEFAULT_MAX_RESULTS))
        except (TypeError, ValueError) as e:
            raise ToolError(f"max_results must be an integer: {e}") from e
        if max_results <= 0:
            raise ToolError("max_results must be positive")

        target = ctx.resolve_within_workspace(path)
        if not target.exists():
            raise ToolError(f"path not found: {path}")

        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise ToolError(f"invalid regex {pattern!r}: {e}") from e

        results: list[str] = []
        truncated = False
        ws_root = ctx.workspace_path.resolve()

        for file_path in self._iter_files(target, glob):
            if len(results) >= max_results:
                truncated = True
                break
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            if size > MAX_GREP_FILE_BYTES:
                continue
            if _looks_binary(file_path):
                continue
            try:
                rel = file_path.relative_to(ws_root)
            except ValueError:
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{rel}:{lineno}:{line.rstrip()}")
                            if len(results) >= max_results:
                                truncated = True
                                break
            except OSError:
                continue

        if not results:
            return f"no matches for {pattern!r} in {path}"
        suffix = f"\n[truncated at {max_results} results]" if truncated else ""
        return "\n".join(results) + suffix

    @staticmethod
    def _iter_files(root: Path, glob: str) -> Iterator[Path]:
        if root.is_file():
            if fnmatch.fnmatch(root.name, glob):
                yield root
            return
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if not fnmatch.fnmatch(p.name, glob):
                continue
            yield p


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(1024)
    except OSError:
        return True
    return b"\x00" in chunk


class FindSiblingTool(Tool):
    spec = ToolSpec(
        name="find_sibling",
        description=(
            "Given a pipeline name, return the most similar existing sibling pipeline "
            "folders under search_root, ranked by name similarity."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "search_root": {
                    "type": "string",
                    "description": (
                        "Directory under the workspace that holds pipeline folders, "
                        "e.g., 'repos/pipelines-reports/src/main/scala/pipelines'."
                    ),
                },
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["pipeline_name", "search_root"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        name = arguments.get("pipeline_name")
        if not isinstance(name, str) or not name:
            raise ToolError("find_sibling requires a non-empty 'pipeline_name'")
        search_root = arguments.get("search_root")
        if not isinstance(search_root, str) or not search_root:
            raise ToolError("find_sibling requires a non-empty 'search_root'")
        try:
            top_k = int(arguments.get("top_k", 5))
        except (TypeError, ValueError) as e:
            raise ToolError(f"top_k must be an integer: {e}") from e
        if top_k <= 0:
            raise ToolError("top_k must be positive")

        target = ctx.resolve_within_workspace(search_root)
        if not target.is_dir():
            raise ToolError(f"search_root is not a directory: {search_root}")

        candidates = [
            d.name
            for d in target.iterdir()
            if d.is_dir() and not d.name.startswith(".") and d.name != name
        ]
        if not candidates:
            return f"no sibling pipelines found in {search_root}"

        scored = sorted(
            ((difflib.SequenceMatcher(None, name, c).ratio(), c) for c in candidates),
            reverse=True,
        )[:top_k]
        lines = [f"sibling pipelines in {search_root} (ranked by name similarity):"]
        for score, candidate in scored:
            lines.append(f"  {score:.2f}\t{candidate}")
        return "\n".join(lines)
