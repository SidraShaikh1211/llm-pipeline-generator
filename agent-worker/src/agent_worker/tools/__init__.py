from agent_worker.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolSpec,
)
from agent_worker.tools.codebase import (
    ReadConventionsTool,
    ReadReferenceTool,
    ReadSkillTool,
)
from agent_worker.tools.emit import ScaffoldTestFixtureTool, WriteFileTool
from agent_worker.tools.repo_nav import (
    FindSiblingTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
)
from agent_worker.tools.test_devbox import RunDevboxTestsTool
from agent_worker.tools.test_local import RunLocalTestsTool
from agent_worker.tools.validate import (
    ConventionCheckTool,
    ScalacTool,
    ScalafmtTool,
)


def builtin_registry() -> ToolRegistry:
    """Registry pre-populated with the worker's built-in passive + emit + validate tools."""
    registry = ToolRegistry()
    for tool_cls in (
        ReadFileTool,
        ListDirTool,
        GrepTool,
        FindSiblingTool,
        ReadSkillTool,
        ReadReferenceTool,
        ReadConventionsTool,
        WriteFileTool,
        ScaffoldTestFixtureTool,
        ScalafmtTool,
        ScalacTool,
        ConventionCheckTool,
        RunLocalTestsTool,
        RunDevboxTestsTool,
    ):
        registry.register(tool_cls())
    return registry


__all__ = [
    "ConventionCheckTool",
    "FindSiblingTool",
    "GrepTool",
    "ListDirTool",
    "ReadConventionsTool",
    "ReadFileTool",
    "ReadReferenceTool",
    "ReadSkillTool",
    "RunDevboxTestsTool",
    "RunLocalTestsTool",
    "ScaffoldTestFixtureTool",
    "ScalacTool",
    "ScalafmtTool",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "ToolSpec",
    "WriteFileTool",
    "builtin_registry",
]
