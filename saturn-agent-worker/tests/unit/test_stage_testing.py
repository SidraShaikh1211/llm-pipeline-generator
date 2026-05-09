from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from saturn_agent.agent.client import FakeLLMClient
from saturn_agent.agent.prompt_builder import PromptBuilder
from saturn_agent.agent.token_budget import TokenBudget
from saturn_agent.orchestrator import (
    JobContext,
    Orchestrator,
    OrchestratorDeps,
    Stage,
)
from saturn_agent.orchestrator.stages import partition_tests, stage_testing
from saturn_agent.persistence import InMemoryJobRepository
from saturn_agent.tools import builtin_registry


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def _build_deps(workspace: Path) -> OrchestratorDeps:
    return OrchestratorDeps(
        llm_client=FakeLLMClient([]),
        registry=builtin_registry(),
        budget=TokenBudget(max_total=10_000),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=SKILLS_ROOT / "references",
        repository=InMemoryJobRepository(),
    )


def _ctx(workspace: Path, generated: list[str]) -> JobContext:
    ctx = JobContext(
        spec={"pipeline_name": "foo"},
        target_repo="saturn-pipelines-reports",
        workspace_path=workspace,
        user_id="u",
    )
    ctx.generated_files = generated
    # Repository must already have the job created (orchestrator does this normally;
    # for handler-level tests we set it up by hand).
    return ctx


def _seed_job(deps: OrchestratorDeps, ctx: JobContext) -> None:
    deps.repository.create_job(
        job_id=ctx.job_id,
        user_id=ctx.user_id,
        spec=ctx.spec,
        target_repo=ctx.target_repo,
        workspace_path=str(ctx.workspace_path),
    )


# ---------- partition_tests ----------


def test_partition_separates_spark_from_pure(workspace: Path) -> None:
    pure_path = workspace / "src/test/scala/PureSpec.scala"
    spark_path = workspace / "src/test/scala/SparkSpec.scala"
    pure_path.parent.mkdir(parents=True)
    pure_path.write_text("class PureSpec { val x = 1 }\n")
    spark_path.write_text(
        "import org.apache.spark.sql.SparkSession\nclass SparkSpec {}\n"
    )
    pure, spark = partition_tests(
        ["src/test/scala/PureSpec.scala", "src/test/scala/SparkSpec.scala"],
        workspace,
    )
    assert pure == ["src/test/scala/PureSpec.scala"]
    assert spark == ["src/test/scala/SparkSpec.scala"]


def test_partition_skips_missing_files(workspace: Path) -> None:
    pure, spark = partition_tests(["src/test/scala/MissingSpec.scala"], workspace)
    assert pure == []
    assert spark == []


# ---------- stage_testing ----------


def test_skips_when_no_test_files(workspace: Path) -> None:
    deps = _build_deps(workspace)
    ctx = _ctx(workspace, generated=["FooNode.scala"])
    _seed_job(deps, ctx)
    assert stage_testing(ctx, deps) == Stage.SUCCEEDED
    types = [e.event_type for e in deps.repository.list_events(ctx.job_id)]
    assert "testing.skipped" in types


def test_passes_when_tools_succeed(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _build_deps(workspace)
    test_path = workspace / "src/test/scala/FooSpec.scala"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("class FooSpec { val x = 1 }\n")
    ctx = _ctx(workspace, generated=["src/test/scala/FooSpec.scala"])
    _seed_job(deps, ctx)

    # Stub run_local_tests to return ok without invoking sbt.
    from saturn_agent.tools.base import ToolContext

    def _fake_invoke(name: str, args: dict, tctx: ToolContext) -> str:
        return "sbt test ok (1 specs)"

    monkeypatch.setattr(deps.registry, "invoke", _fake_invoke)
    assert stage_testing(ctx, deps) == Stage.SUCCEEDED
    events = [e.event_type for e in deps.repository.list_events(ctx.job_id)]
    assert "testing.passed" in events


def test_failure_returns_repairing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _build_deps(workspace)
    test_path = workspace / "src/test/scala/FooSpec.scala"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("class FooSpec { val x = 1 }\n")
    ctx = _ctx(workspace, generated=["src/test/scala/FooSpec.scala"])
    _seed_job(deps, ctx)

    monkeypatch.setattr(
        deps.registry,
        "invoke",
        lambda name, args, tctx: "sbt test failed (rc=1):\n[error] FooSpec FAILED",
    )
    assert stage_testing(ctx, deps) == Stage.REPAIRING
    assert ctx.test_attempts == 1
    assert "FooSpec FAILED" in (ctx.error or "")


def test_failure_after_max_attempts_returns_failed(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _build_deps(workspace)
    test_path = workspace / "src/test/scala/FooSpec.scala"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("class FooSpec { val x = 1 }\n")
    ctx = _ctx(workspace, generated=["src/test/scala/FooSpec.scala"])
    ctx.test_attempts = Orchestrator.MAX_TEST_ATTEMPTS - 1  # next call hits the cap
    _seed_job(deps, ctx)

    monkeypatch.setattr(
        deps.registry,
        "invoke",
        lambda name, args, tctx: "sbt test failed (rc=1):\nfail",
    )
    assert stage_testing(ctx, deps) == Stage.FAILED


def test_devbox_skip_message_does_not_count_as_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spark tests with no devbox configured should NOT fail the stage."""
    deps = _build_deps(workspace)
    test_path = workspace / "src/test/scala/SparkSpec.scala"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("import org.apache.spark.sql.SparkSession\n")
    ctx = _ctx(workspace, generated=["src/test/scala/SparkSpec.scala"])
    _seed_job(deps, ctx)

    def _fake_invoke(name: str, args: dict, tctx: Any) -> str:
        if name == "run_devbox_tests":
            return "no devbox configured; skipping 1 Spark spec(s)"
        return "sbt test ok"

    monkeypatch.setattr(deps.registry, "invoke", _fake_invoke)
    assert stage_testing(ctx, deps) == Stage.SUCCEEDED
