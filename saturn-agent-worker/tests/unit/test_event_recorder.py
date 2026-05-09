from __future__ import annotations

import json
import uuid

import pytest

from saturn_agent.agent.event_recorder import (
    JobEventRecorder,
    NoOpEventRecorder,
    summarize_args,
)
from saturn_agent.observability.logging import (
    bind_job_context,
    configure_logging,
    reset_logging,
)
from saturn_agent.persistence.repository import InMemoryJobRepository


@pytest.fixture(autouse=True)
def _reset_after() -> None:
    yield
    reset_logging()


def test_noop_does_nothing() -> None:
    NoOpEventRecorder().emit("anything", {"k": "v"})
    # nothing to assert — by definition; fixture confirms no exception


def test_job_event_recorder_writes_to_repository() -> None:
    repo = InMemoryJobRepository()
    job_id = uuid.uuid4()
    repo.create_job(
        job_id=job_id,
        user_id="u",
        spec={},
        target_repo="r",
        workspace_path=None,
    )
    rec = JobEventRecorder(repo, job_id, "planning")
    rec.emit("llm.called", {"tokens_used": 10})
    rec.emit("tool.invoked", {"tool": "read_file"})
    events = repo.list_events(job_id)
    assert [e.event_type for e in events] == ["llm.called", "tool.invoked"]
    assert events[0].stage == "planning"
    assert events[0].payload == {"tokens_used": 10}


def test_job_event_recorder_logs_structured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_output=True)
    bind_job_context(job_id="job-x", user_id="alice")
    repo = InMemoryJobRepository()
    job_id = uuid.uuid4()
    repo.create_job(
        job_id=job_id,
        user_id="alice",
        spec={},
        target_repo="r",
        workspace_path=None,
    )
    rec = JobEventRecorder(repo, job_id, "planning")
    rec.emit("llm.called", {"tokens_used": 17})

    last = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(last)
    assert payload["event"] == "llm.called"
    assert payload["tokens_used"] == 17
    # contextvars carried by structlog (job_id from bind_job_context)
    assert payload["job_id"] == "job-x"


def test_summarize_args_truncates_long_strings() -> None:
    out = summarize_args(
        {"path": "FooNode.scala", "content": "x" * 200}, max_value_len=50
    )
    assert out["path"] == "FooNode.scala"
    assert out["content"].startswith("x" * 50)
    assert "...(200 chars)" in out["content"]


def test_summarize_args_compacts_containers() -> None:
    out = summarize_args(
        {"paths": ["a", "b", "c"], "config": {"x": 1, "y": 2}, "n": 5}
    )
    assert out["paths"] == "<list len=3>"
    assert out["config"] == "<dict keys=2>"
    assert out["n"] == 5


def test_summarize_args_passes_short_strings_through() -> None:
    out = summarize_args({"name": "alice", "age": "30"})
    assert out == {"name": "alice", "age": "30"}
