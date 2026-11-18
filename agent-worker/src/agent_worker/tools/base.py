from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class ToolError(Exception):
    """Raised by a tool when it cannot complete. Message is shown to the LLM."""


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters_schema: dict[str, Any]


class ToolContext(BaseModel):
    job_id: str
    workspace_path: Path
    target_repo_path: Path
    devbox_host: str | None = None
    user_id: str

    # Worker-side resources (read-only). Optional so callers can omit them
    # for tools that don't need them.
    skills_root: Path | None = None
    references_root: Path | None = None
    conventions_path: Path | None = None

    model_config = {"arbitrary_types_allowed": True}

    def resolve_within_workspace(self, path: str | Path) -> Path:
        """Resolve a path against workspace_path; reject anything that escapes it.

        Used by emit/read tools to enforce the sandbox (LLD §6.6).
        """
        return self._resolve_within_root(path, self.workspace_path, "workspace")

    def resolve_within_root(self, path: str | Path, root: Path, root_label: str) -> Path:
        """Resolve a path under an arbitrary read-only root and reject traversal.

        Used by read_skill/read_reference, which read from the worker's bundled
        skills folder (not the workspace).
        """
        return self._resolve_within_root(path, root, root_label)

    @staticmethod
    def _resolve_within_root(path: str | Path, root: Path, label: str) -> Path:
        candidate = (root / Path(path)).resolve()
        root_resolved = root.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError as e:
            raise ToolError(
                f"path escapes {label}: {path!r} resolves to {candidate} "
                f"which is outside {root_resolved}"
            ) from e
        return candidate


class Tool(ABC):
    spec: ToolSpec

    @abstractmethod
    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        """Returns stringified result. Raises ToolError on user-facing failure."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"tool already registered: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def spec(self, name: str) -> ToolSpec:
        return self.get(name).spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def invoke(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> str:
        return self.get(name).invoke(arguments, ctx)
