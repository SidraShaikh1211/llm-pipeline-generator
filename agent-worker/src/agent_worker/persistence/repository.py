from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass
class JobRecord:
    job_id: uuid.UUID
    user_id: str
    spec: dict[str, Any]
    target_repo: str
    stage: str
    workspace_path: str | None = None
    devbox_host: str | None = None
    plan: dict[str, Any] | None = None
    pr_url: str | None = None
    error: str | None = None
    open_questions: list[str] = field(default_factory=list)
    tokens_used: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def to_json_safe(self) -> dict[str, Any]:
        d = asdict(self)
        d["job_id"] = str(self.job_id)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        d["finished_at"] = (
            self.finished_at.isoformat() if self.finished_at is not None else None
        )
        return d


@dataclass
class JobEventRecord:
    job_id: uuid.UUID
    stage: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json_safe(self) -> dict[str, Any]:
        return {
            "job_id": str(self.job_id),
            "stage": self.stage,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
        }


class JobRepository(Protocol):
    """Persistence boundary for jobs and their event tape.

    Implementations: InMemoryJobRepository for CLI/dev/tests, future
    SqlAlchemyJobRepository for production. Both honor the same surface so
    the orchestrator depends only on this Protocol.
    """

    def create_job(
        self,
        *,
        job_id: uuid.UUID,
        user_id: str,
        spec: dict[str, Any],
        target_repo: str,
        workspace_path: str | None,
        devbox_host: str | None = None,
        stage: str = "init",
    ) -> JobRecord: ...

    def get_job(self, job_id: uuid.UUID) -> JobRecord | None: ...

    def update_job(
        self,
        job_id: uuid.UUID,
        *,
        stage: str | None = None,
        plan: dict[str, Any] | None = None,
        pr_url: str | None = None,
        error: str | None = None,
        open_questions: list[str] | None = None,
        tokens_used: int | None = None,
        finished_at: datetime | None = None,
    ) -> JobRecord: ...

    def append_event(
        self,
        *,
        job_id: uuid.UUID,
        stage: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> JobEventRecord: ...

    def list_events(self, job_id: uuid.UUID) -> list[JobEventRecord]: ...


class InMemoryJobRepository:
    """Thread-safe in-memory JobRepository for CLI/dev/tests.

    Drop-in for the Protocol; identical surface to the future Postgres-backed
    SqlAlchemyJobRepository.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[uuid.UUID, JobRecord] = {}
        self._events: dict[uuid.UUID, list[JobEventRecord]] = defaultdict(list)

    def create_job(
        self,
        *,
        job_id: uuid.UUID,
        user_id: str,
        spec: dict[str, Any],
        target_repo: str,
        workspace_path: str | None,
        devbox_host: str | None = None,
        stage: str = "init",
    ) -> JobRecord:
        with self._lock:
            if job_id in self._jobs:
                raise ValueError(f"job already exists: {job_id}")
            now = datetime.now(UTC)
            record = JobRecord(
                job_id=job_id,
                user_id=user_id,
                spec=dict(spec),
                target_repo=target_repo,
                stage=stage,
                workspace_path=workspace_path,
                devbox_host=devbox_host,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job_id] = record
            return record

    def get_job(self, job_id: uuid.UUID) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update_job(
        self,
        job_id: uuid.UUID,
        *,
        stage: str | None = None,
        plan: dict[str, Any] | None = None,
        pr_url: str | None = None,
        error: str | None = None,
        open_questions: list[str] | None = None,
        tokens_used: int | None = None,
        finished_at: datetime | None = None,
    ) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(f"unknown job: {job_id}")
            if stage is not None:
                record.stage = stage
            if plan is not None:
                record.plan = plan
            if pr_url is not None:
                record.pr_url = pr_url
            if error is not None:
                record.error = error
            if open_questions is not None:
                record.open_questions = list(open_questions)
            if tokens_used is not None:
                record.tokens_used = tokens_used
            if finished_at is not None:
                record.finished_at = finished_at
            record.updated_at = datetime.now(UTC)
            return record

    def append_event(
        self,
        *,
        job_id: uuid.UUID,
        stage: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> JobEventRecord:
        with self._lock:
            event = JobEventRecord(
                job_id=job_id,
                stage=stage,
                event_type=event_type,
                payload=dict(payload or {}),
            )
            self._events[job_id].append(event)
            return event

    def list_events(self, job_id: uuid.UUID) -> list[JobEventRecord]:
        with self._lock:
            return list(self._events.get(job_id, []))
