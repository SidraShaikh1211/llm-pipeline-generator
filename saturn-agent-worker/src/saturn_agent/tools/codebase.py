from __future__ import annotations

import json
from typing import Any

from saturn_agent.tools.base import Tool, ToolContext, ToolError, ToolSpec


class ReadSkillTool(Tool):
    spec = ToolSpec(
        name="read_skill",
        description=(
            "Return the SKILL.md for a named skill bundled with the worker, e.g., "
            "'saturn-pipeline-builder'. Skills tell the agent how to write Saturn code."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name, matches a folder under skills_root.",
                },
            },
            "required": ["name"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        if ctx.skills_root is None:
            raise ToolError("skills_root is not configured for this job")
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            raise ToolError("read_skill requires a non-empty 'name'")
        skill_dir = ctx.resolve_within_root(name, ctx.skills_root, "skills_root")
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            raise ToolError(f"skill not found: {name} (looked at {skill_md})")
        return skill_md.read_text(encoding="utf-8", errors="replace")


class ReadReferenceTool(Tool):
    spec = ToolSpec(
        name="read_reference",
        description=(
            "Return the contents of a reference doc bundled with the worker. "
            "References are short topical files (style guides, patterns, examples)."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Reference filename, with or without '.md' suffix "
                        "(e.g., 'partitioning_guide' or 'partitioning_guide.md')."
                    ),
                },
            },
            "required": ["name"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        if ctx.references_root is None:
            raise ToolError("references_root is not configured for this job")
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            raise ToolError("read_reference requires a non-empty 'name'")

        candidates = [name]
        if not name.endswith(".md"):
            candidates.append(f"{name}.md")
        for candidate in candidates:
            ref_path = ctx.resolve_within_root(
                candidate, ctx.references_root, "references_root"
            )
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8", errors="replace")
        raise ToolError(f"reference not found: {name}")


class ReadConventionsTool(Tool):
    spec = ToolSpec(
        name="read_conventions",
        description=(
            "Return the conventions JSON for the target repo. "
            "Optionally filter to one pipeline_domain to keep the prompt small."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "pipeline_domain": {
                    "type": "string",
                    "description": (
                        "Pipeline name (e.g., 'foo_reports'). When set, only that "
                        "pipeline's conventions are returned. Omit to get the full repo blob."
                    ),
                },
            },
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        if ctx.conventions_path is None:
            raise ToolError("conventions_path is not configured for this job")
        if not ctx.conventions_path.is_file():
            raise ToolError(f"conventions file not found: {ctx.conventions_path}")

        try:
            data = json.loads(ctx.conventions_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ToolError(f"conventions file is not valid JSON: {e}") from e

        domain = arguments.get("pipeline_domain")
        if not domain:
            return json.dumps(data, indent=2)

        pipelines = data.get("pipelines", []) if isinstance(data, dict) else []
        matching = [
            p for p in pipelines if isinstance(p, dict) and p.get("pipeline_name") == domain
        ]
        if not matching:
            available = sorted(
                p.get("pipeline_name", "?") for p in pipelines if isinstance(p, dict)
            )
            raise ToolError(
                f"no pipeline named {domain!r}. available: {', '.join(available) or 'none'}"
            )
        sliced = {
            **{k: v for k, v in data.items() if k != "pipelines"},
            "pipelines": matching,
        }
        return json.dumps(sliced, indent=2)
