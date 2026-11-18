from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, ClassVar

from agent_worker.tools.base import Tool, ToolContext, ToolError, ToolSpec


SBT_TIMEOUT_S = 600
_OUTPUT_TAIL_LINES = 200  # cap on what we hand back to the agent


class RunLocalTestsTool(Tool):
    BINARY: ClassVar[str] = "sbt"

    spec = ToolSpec(
        name="run_local_tests",
        description=(
            "Run pure-Scala tests via sbt in the workspace. "
            "If specs is omitted or ['all'], runs `sbt test`; otherwise runs "
            "`sbt 'testOnly *<spec>*'` for each spec. Skips when sbt isn't installed."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "specs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Test class names or file paths; pass ['all'] for full suite."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Workspace-relative directory to run sbt in (defaults to workspace root)."
                    ),
                },
            },
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        binary = shutil.which(self.BINARY)
        if binary is None:
            return f"{self.BINARY} not installed; skipping local tests"

        cwd = self._resolve_cwd(arguments.get("cwd"), ctx)
        specs = arguments.get("specs") or []
        if not isinstance(specs, list) or any(not isinstance(s, str) for s in specs):
            raise ToolError("'specs' must be a list of strings")

        cmd = self._build_cmd(binary, specs)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=SBT_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"sbt timed out after {SBT_TIMEOUT_S}s"
        except FileNotFoundError as e:
            raise ToolError(f"sbt not runnable: {e}") from e

        combined = (result.stderr + result.stdout).strip()
        tail = "\n".join(combined.splitlines()[-_OUTPUT_TAIL_LINES:])
        if result.returncode != 0:
            return f"sbt test failed (rc={result.returncode}):\n{tail}"
        return f"sbt test ok ({len(specs) or 'all'} specs)\n{tail}".rstrip()

    @staticmethod
    def _resolve_cwd(raw: str | None, ctx: ToolContext) -> Path:
        if raw:
            target = ctx.resolve_within_workspace(raw)
            if not target.is_dir():
                raise ToolError(f"cwd is not a directory: {raw}")
            return target
        # Default: prefer the cloned target repo if it's an sbt project, since
        # `sbt test` needs build.sbt at cwd. Fall back to workspace root for
        # the no-clone path.
        repo_dir = ctx.target_repo_path
        if repo_dir and repo_dir.is_dir() and (repo_dir / "build.sbt").is_file():
            return repo_dir.resolve()
        return ctx.workspace_path.resolve()

    @staticmethod
    def _build_cmd(binary: str, specs: list[str]) -> list[str]:
        if not specs or specs == ["all"]:
            return [binary, "test"]
        # `sbt testOnly` matches against class names, not file paths. The
        # orchestrator passes file paths like
        # 'repos/<repo>/src/test/scala/pipelines/foo/FooSpec.scala'; reduce each
        # to its basename without extension before wrapping in glob wildcards.
        names = [_spec_to_class_glob(s) for s in specs if s]
        joined = " ".join(names)
        return [binary, f"testOnly {joined}"]


def _spec_to_class_glob(spec: str) -> str:
    """Turn a file path or bare class name into an sbt testOnly glob.

    'src/test/scala/pipelines/foo/FooSpec.scala' -> '*FooSpec*'
    'pipelines.foo.FooSpec'                      -> '*FooSpec*'  (drop package)
    'FooSpec'                                 -> '*FooSpec*'
    """
    name = spec
    if "/" in name or name.endswith(".scala"):
        name = Path(name).stem  # strip dirs + .scala extension
    if "." in name:
        name = name.rsplit(".", 1)[-1]  # strip package prefix
    return f"*{name}*"
