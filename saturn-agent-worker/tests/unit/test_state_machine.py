from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from saturn_agent.orchestrator.state_machine import (
    JobContext,
    Orchestrator,
    Stage,
    TERMINAL_STAGES,
)
from saturn_agent.persistence import InMemoryJobRepository, JobRepository


@dataclass
class _StubDeps:
    repository: JobRepository


def _ctx(workspace: Path) -> JobContext:
    return JobContext(
        spec={"pipeline_name": "foo"},
        target_repo="saturn-pipelines-reports",
        workspace_path=workspace,
        user_id="local-dev",
    )


def _stub_deps() -> _StubDeps:
    return _StubDeps(repository=InMemoryJobRepository())


def test_unhandled_stage_falls_to_failed(workspace: Path) -> None:
    orc = Orchestrator(_ctx(workspace), deps=_stub_deps(), handlers={})  # type: ignore[arg-type]
    final = orc.run()
    assert final == Stage.FAILED


def test_dispatch_walks_to_succeeded(workspace: Path) -> None:
    handlers = {
        Stage.INIT: lambda c, d: Stage.PLANNING,
        Stage.PLANNING: lambda c, d: Stage.GENERATING,
        Stage.GENERATING: lambda c, d: Stage.SUCCEEDED,
    }
    orc = Orchestrator(_ctx(workspace), deps=_stub_deps(), handlers=handlers)  # type: ignore[arg-type]
    assert orc.run() == Stage.SUCCEEDED


def test_dispatch_walks_to_paused(workspace: Path) -> None:
    handlers = {
        Stage.INIT: lambda c, d: Stage.PLANNING,
        Stage.PLANNING: lambda c, d: Stage.PAUSED_FOR_USER,
    }
    orc = Orchestrator(_ctx(workspace), deps=_stub_deps(), handlers=handlers)  # type: ignore[arg-type]
    assert orc.run() == Stage.PAUSED_FOR_USER


def test_records_stage_events(workspace: Path) -> None:
    deps = _stub_deps()
    handlers = {
        Stage.INIT: lambda c, d: Stage.PLANNING,
        Stage.PLANNING: lambda c, d: Stage.SUCCEEDED,
    }
    ctx = _ctx(workspace)
    Orchestrator(ctx, deps=deps, handlers=handlers).run()  # type: ignore[arg-type]

    events = deps.repository.list_events(ctx.job_id)
    types = [e.event_type for e in events]
    assert types[0] == "job.created"
    assert "stage.entered" in types
    assert "stage.exited" in types
    assert types[-1] == "job.succeeded"


def test_records_terminal_failure(workspace: Path) -> None:
    deps = _stub_deps()
    ctx = _ctx(workspace)
    Orchestrator(ctx, deps=deps, handlers={}).run()  # type: ignore[arg-type]
    types = [e.event_type for e in deps.repository.list_events(ctx.job_id)]
    assert types[-1] == "job.failed"


def test_persisted_record_reflects_final_state(workspace: Path) -> None:
    deps = _stub_deps()
    handlers = {
        Stage.INIT: lambda c, d: Stage.SUCCEEDED,
    }
    ctx = _ctx(workspace)
    Orchestrator(ctx, deps=deps, handlers=handlers).run()  # type: ignore[arg-type]

    record = deps.repository.get_job(ctx.job_id)
    assert record is not None
    assert record.stage == "succeeded"
    assert record.finished_at is not None


def test_terminal_stages_set() -> None:
    assert Stage.SUCCEEDED in TERMINAL_STAGES
    assert Stage.FAILED in TERMINAL_STAGES
    assert Stage.PAUSED_FOR_USER in TERMINAL_STAGES
    assert Stage.PLANNING not in TERMINAL_STAGES
