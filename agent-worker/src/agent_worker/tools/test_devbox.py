from __future__ import annotations

from typing import Any

from agent_worker.tools.base import Tool, ToolContext, ToolError, ToolSpec


class RunDevboxTestsTool(Tool):
    """Stub. Real SSH impl lands once we have a devbox to integration-test against.

    Until then this returns a clear "not wired" message so the orchestrator can
    distinguish "no devbox configured" from "devbox tests failed". When wiring up
    for real, replace the body with a paramiko/asyncssh client that runs sbt on
    the devbox and streams output back.
    """

    spec = ToolSpec(
        name="run_devbox_tests",
        description=(
            "Run Spark-dependent Scala tests on the requesting user's devbox over SSH. "
            "STUB: currently returns a notice; implement against your firm's devbox auth model."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "specs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        specs = arguments.get("specs") or []
        if not isinstance(specs, list) or any(not isinstance(s, str) for s in specs):
            raise ToolError("'specs' must be a list of strings")

        if not ctx.devbox_host:
            return f"no devbox configured; skipping {len(specs) or 'all'} Spark spec(s)"
        return (
            f"devbox tests not yet wired up (would target {ctx.devbox_host} "
            f"with {len(specs) or 'all'} spec(s)); skipping"
        )
