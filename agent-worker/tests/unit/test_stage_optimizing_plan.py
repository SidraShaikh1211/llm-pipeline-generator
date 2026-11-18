from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_worker.orchestrator import JobContext, Stage
from agent_worker.orchestrator.stages import stage_optimizing_plan
from agent_worker.persistence import InMemoryJobRepository, JobRepository


@dataclass
class _StubDeps:
    """Minimal OrchestratorDeps surface stage_optimizing_plan actually uses."""

    repository: JobRepository


def _ctx(workspace: Path, plan: dict | None) -> JobContext:
    ctx = JobContext(
        spec={"pipeline_name": "foo_reports"},
        target_repo="pipelines-reports",
        workspace_path=workspace,
        user_id="local-dev",
    )
    ctx.plan = plan
    return ctx


def _file(path: str, depends_on: list[str] | None = None) -> dict:
    return {
        "path": path,
        "kind": "scala_node",
        "style": None,
        "role": None,
        "depends_on": depends_on or [],
        "description": "stub",
    }


def _plan_dict(files: list[dict]) -> dict:
    return {
        "pipeline_name": "foo_reports",
        "target_repo": "pipelines-reports",
        "skills_required": [],
        "files": files,
        "ddls": [],
        "assumptions": [],
        "open_questions": [],
        "estimated_tokens": 0,
    }


def test_optimizing_plan_topo_sorts_and_records_event(workspace: Path) -> None:
    plan = _plan_dict([
        _file("C.scala", depends_on=["B.scala"]),
        _file("A.scala"),
        _file("B.scala", depends_on=["A.scala"]),
    ])
    deps = _StubDeps(repository=InMemoryJobRepository())
    ctx = _ctx(workspace, plan)
    deps.repository.create_job(
        job_id=ctx.job_id,
        user_id=ctx.user_id,
        spec=ctx.spec,
        target_repo=ctx.target_repo,
        workspace_path=str(ctx.workspace_path),
    )

    next_stage = stage_optimizing_plan(ctx, deps)  # type: ignore[arg-type]

    assert next_stage == Stage.GENERATING, ctx.error
    assert ctx.plan is not None
    paths = [f["path"] for f in ctx.plan["files"]]
    assert paths == ["A.scala", "B.scala", "C.scala"]

    events = deps.repository.list_events(ctx.job_id)
    validated = [e for e in events if e.event_type == "dag.validated"]
    assert len(validated) == 1
    assert validated[0].payload["order"] == ["A.scala", "B.scala", "C.scala"]


def test_optimizing_plan_cycle_fails_and_records_invalid(workspace: Path) -> None:
    plan = _plan_dict([
        _file("A.scala", depends_on=["B.scala"]),
        _file("B.scala", depends_on=["A.scala"]),
    ])
    deps = _StubDeps(repository=InMemoryJobRepository())
    ctx = _ctx(workspace, plan)
    deps.repository.create_job(
        job_id=ctx.job_id,
        user_id=ctx.user_id,
        spec=ctx.spec,
        target_repo=ctx.target_repo,
        workspace_path=str(ctx.workspace_path),
    )

    next_stage = stage_optimizing_plan(ctx, deps)  # type: ignore[arg-type]

    assert next_stage == Stage.FAILED
    assert ctx.error is not None
    assert "DAG validation failed" in ctx.error

    events = deps.repository.list_events(ctx.job_id)
    invalid = [e for e in events if e.event_type == "dag.invalid"]
    assert len(invalid) == 1
    assert invalid[0].payload["kind"] == "CycleError"


def test_optimizing_plan_dangling_dep_fails(workspace: Path) -> None:
    plan = _plan_dict([_file("A.scala", depends_on=["Ghost.scala"])])
    deps = _StubDeps(repository=InMemoryJobRepository())
    ctx = _ctx(workspace, plan)
    deps.repository.create_job(
        job_id=ctx.job_id,
        user_id=ctx.user_id,
        spec=ctx.spec,
        target_repo=ctx.target_repo,
        workspace_path=str(ctx.workspace_path),
    )

    next_stage = stage_optimizing_plan(ctx, deps)  # type: ignore[arg-type]

    assert next_stage == Stage.FAILED
    events = deps.repository.list_events(ctx.job_id)
    invalid = [e for e in events if e.event_type == "dag.invalid"]
    assert len(invalid) == 1
    assert invalid[0].payload["kind"] == "DanglingDepError"


def test_optimizing_plan_no_plan_fails(workspace: Path) -> None:
    deps = _StubDeps(repository=InMemoryJobRepository())
    ctx = _ctx(workspace, plan=None)
    deps.repository.create_job(
        job_id=ctx.job_id,
        user_id=ctx.user_id,
        spec=ctx.spec,
        target_repo=ctx.target_repo,
        workspace_path=str(ctx.workspace_path),
    )

    next_stage = stage_optimizing_plan(ctx, deps)  # type: ignore[arg-type]

    assert next_stage == Stage.FAILED
    assert ctx.error == "optimizing_plan entered with no plan"
