from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest

from agent_worker.persistence.repository import (
    InMemoryJobRepository,
    JobRecord,
    JobRepository,
)


def _make() -> tuple[InMemoryJobRepository, uuid.UUID]:
    repo = InMemoryJobRepository()
    job_id = uuid.uuid4()
    repo.create_job(
        job_id=job_id,
        user_id="alice",
        spec={"pipeline_name": "foo"},
        target_repo="pipelines-reports",
        workspace_path="/tmp/ws",
    )
    return repo, job_id


def test_create_and_get_job() -> None:
    repo, job_id = _make()
    record = repo.get_job(job_id)
    assert record is not None
    assert record.user_id == "alice"
    assert record.spec == {"pipeline_name": "foo"}
    assert record.stage == "init"
    assert record.tokens_used == 0
    assert record.finished_at is None


def test_create_duplicate_raises() -> None:
    repo, job_id = _make()
    with pytest.raises(ValueError, match="already exists"):
        repo.create_job(
            job_id=job_id,
            user_id="bob",
            spec={},
            target_repo="x",
            workspace_path=None,
        )


def test_get_unknown_returns_none() -> None:
    repo = InMemoryJobRepository()
    assert repo.get_job(uuid.uuid4()) is None


def test_update_job_partial_fields() -> None:
    repo, job_id = _make()
    repo.update_job(job_id, stage="planning", tokens_used=1500)
    record = repo.get_job(job_id)
    assert record is not None
    assert record.stage == "planning"
    assert record.tokens_used == 1500
    # Unspecified fields preserved
    assert record.user_id == "alice"


def test_update_unknown_raises() -> None:
    repo = InMemoryJobRepository()
    with pytest.raises(KeyError, match="unknown job"):
        repo.update_job(uuid.uuid4(), stage="x")


def test_finished_at_updates_record() -> None:
    repo, job_id = _make()
    now = datetime.now(UTC)
    repo.update_job(job_id, stage="succeeded", finished_at=now)
    record = repo.get_job(job_id)
    assert record is not None
    assert record.finished_at == now


def test_append_and_list_events() -> None:
    repo, job_id = _make()
    repo.append_event(
        job_id=job_id, stage="planning", event_type="stage.entered", payload={}
    )
    repo.append_event(
        job_id=job_id, stage="planning", event_type="plan.parsed", payload={"files": ["A"]}
    )
    events = repo.list_events(job_id)
    assert [e.event_type for e in events] == ["stage.entered", "plan.parsed"]
    assert events[1].payload == {"files": ["A"]}


def test_list_events_unknown_returns_empty() -> None:
    repo = InMemoryJobRepository()
    assert repo.list_events(uuid.uuid4()) == []


def test_to_json_safe() -> None:
    repo, job_id = _make()
    repo.append_event(
        job_id=job_id, stage="init", event_type="job.created", payload={"x": 1}
    )
    record = repo.get_job(job_id)
    assert record is not None
    payload = record.to_json_safe()
    assert payload["job_id"] == str(job_id)
    assert isinstance(payload["created_at"], str)


def test_concurrent_append_events() -> None:
    repo, job_id = _make()

    def writer(n: int) -> None:
        for i in range(50):
            repo.append_event(
                job_id=job_id,
                stage="planning",
                event_type="x",
                payload={"writer": n, "i": i},
            )

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = repo.list_events(job_id)
    assert len(events) == 200


def test_in_memory_satisfies_protocol() -> None:
    repo: JobRepository = InMemoryJobRepository()
    assert callable(repo.create_job)
    assert callable(repo.append_event)
