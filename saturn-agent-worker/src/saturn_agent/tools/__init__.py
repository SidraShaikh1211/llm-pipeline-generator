from saturn_agent.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolSpec,
)
from saturn_agent.tools.codebase import (
    ReadConventionsTool,
    ReadReferenceTool,
    ReadSkillTool,
)
from saturn_agent.tools.emit import ScaffoldTestFixtureTool, WriteFileTool
from saturn_agent.tools.repo_nav import (
    FindSiblingTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
)
from saturn_agent.tools.test_devbox import RunDevboxTestsTool
from saturn_agent.tools.test_local import RunLocalTestsTool
from saturn_agent.tools.validate import (
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
