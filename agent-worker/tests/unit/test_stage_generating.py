from __future__ import annotations

from pathlib import Path

import pytest

from agent_worker.agent.client import FakeLLMClient
from agent_worker.agent.prompt_builder import PromptBuilder
from agent_worker.agent.token_budget import TokenBudget
from agent_worker.agent.types import GenerateResult, ToolCall
from agent_worker.orchestrator import (
    JobContext,
    OrchestratorDeps,
    Stage,
)
from agent_worker.orchestrator.stages import stage_generating
from agent_worker.persistence import InMemoryJobRepository
from agent_worker.tools import builtin_registry


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def _deps(llm: FakeLLMClient) -> OrchestratorDeps:
    return OrchestratorDeps(
        llm_client=llm,
        registry=builtin_registry(),
        budget=TokenBudget(max_total=10_000),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=SKILLS_ROOT / "references",
        repository=InMemoryJobRepository(),
    )


def _ctx(workspace: Path, plan_path: str) -> JobContext:
    ctx = JobContext(
        spec={"pipeline_name": "foo_reports"},
        target_repo="pipelines-reports",
        workspace_path=workspace,
        user_id="u",
    )
    ctx.plan = {
        "pipeline_name": "foo_reports",
        "target_repo": "pipelines-reports",
        "skills_required": ["pipeline-builder"],
        "files": [
            {
                "path": plan_path,
                "kind": "scala_node",
                "style": "a_single_node",
                "role": "leaf_transform",
                "depends_on": [],
                "description": "stub",
            }
        ],
        "ddls": [],
        "assumptions": [],
        "open_questions": [],
        "estimated_tokens": 0,
    }
    return ctx


def _seed(deps: OrchestratorDeps, ctx: JobContext) -> None:
    deps.repository.create_job(
        job_id=ctx.job_id,
        user_id=ctx.user_id,
        spec=ctx.spec,
        target_repo=ctx.target_repo,
        workspace_path=str(ctx.workspace_path),
    )


def _write_call(path: str, content: str = "// stub\n") -> GenerateResult:
    return GenerateResult(
        text=None,
        tool_calls=[
            ToolCall(id="w1", name="write_file", arguments={"path": path, "content": content})
        ],
        tokens_used=10,
        finish_reason="tool_calls",
    )


def _final_text() -> GenerateResult:
    return GenerateResult(text="done", tool_calls=[], tokens_used=1, finish_reason="stop")


def test_generating_accepts_file_written_into_cloned_repo(workspace: Path) -> None:
    """Regression: planner emits a repo-relative path; the agent writes the
    file inside <workspace>/repos/<target_repo>/ to inherit the repo's
    .scalafmt.conf and classpath. The orchestrator must accept that layout
    and store the workspace-relative form so the validation tools can
    resolve_within_workspace() the file later."""

    clone_dir = workspace / "repos" / "pipelines-reports"
    clone_dir.mkdir(parents=True)

    plan_path = "src/main/scala/pipelines/foo_reports/FooNode.scala"
    write_path = "repos/pipelines-reports/" + plan_path

    deps = _deps(FakeLLMClient([_write_call(write_path), _final_text()]))
    ctx = _ctx(workspace, plan_path)
    _seed(deps, ctx)

    next_stage = stage_generating(ctx, deps)

    assert next_stage == Stage.VALIDATING, ctx.error
    assert ctx.generated_files == [write_path]
    assert (clone_dir / plan_path).is_file()


def test_generating_accepts_file_written_at_workspace_root(workspace: Path) -> None:
    """When no clone is present, the agent writes at workspace root and
    generated_files records the path as-is. Preserves the no-clone path."""

    plan_path = "FooNode.scala"
    deps = _deps(FakeLLMClient([_write_call(plan_path), _final_text()]))
    ctx = _ctx(workspace, plan_path)
    _seed(deps, ctx)

    next_stage = stage_generating(ctx, deps)

    assert next_stage == Stage.VALIDATING, ctx.error
    assert ctx.generated_files == ["FooNode.scala"]


def test_generating_fails_when_agent_writes_nothing(workspace: Path) -> None:
    """If the agent finishes without writing the planned file at either
    location, the orchestrator records generation.missing_file and fails."""

    plan_path = "src/main/scala/pipelines/foo_reports/FooNode.scala"
    # Agent returns text only, no write_file tool call.
    deps = _deps(FakeLLMClient([_final_text()]))
    ctx = _ctx(workspace, plan_path)
    _seed(deps, ctx)

    next_stage = stage_generating(ctx, deps)

    assert next_stage == Stage.FAILED
    assert "did not write" in (ctx.error or "")
    events = deps.repository.list_events(ctx.job_id)
    assert any(e.event_type == "generation.missing_file" for e in events)


def test_generating_injects_upstream_content_into_user_message(
    workspace: Path,
) -> None:
    """When a FilePlan declares depends_on and the upstream body is already
    in ctx.generated_files_content, the per-file user message must include
    that body so the LLM sees the upstream contract."""

    plan_path = "FooRocketProvider.scala"
    fake = FakeLLMClient([_write_call(plan_path), _final_text()])
    deps = _deps(fake)
    ctx = _ctx(workspace, plan_path)
    # Mark this file as depending on an already-generated upstream node.
    ctx.plan["files"][0]["depends_on"] = ["FooNode.scala"]
    upstream_body = "object FooNode { val SCHEMA = \"id INT\" }"
    ctx.generated_files_content["FooNode.scala"] = upstream_body
    _seed(deps, ctx)

    next_stage = stage_generating(ctx, deps)

    assert next_stage == Stage.VALIDATING, ctx.error
    # First call is the generation request; the user message lives in the
    # last entry of `messages`.
    first_call = fake.calls[0]
    user_msgs = [
        m for m in first_call["messages"] if getattr(m, "role", None) == "user"
    ]
    assert user_msgs, "expected at least one user message"
    body = user_msgs[-1].content
    if isinstance(body, list):
        body = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in body
        )
    assert "Upstream files (already generated)" in body
    assert "FooNode.scala" in body
    assert upstream_body in body


def test_generating_records_upstream_body_after_write(workspace: Path) -> None:
    """After a successful write, the file's content must be stored in
    ctx.generated_files_content keyed by its plan path so downstream files
    can pull it in."""

    plan_path = "FooNode.scala"
    content = "object FooNode { /* generated */ }"
    deps = _deps(FakeLLMClient([_write_call(plan_path, content), _final_text()]))
    ctx = _ctx(workspace, plan_path)
    _seed(deps, ctx)

    next_stage = stage_generating(ctx, deps)

    assert next_stage == Stage.VALIDATING, ctx.error
    assert ctx.generated_files_content.get(plan_path) == content
