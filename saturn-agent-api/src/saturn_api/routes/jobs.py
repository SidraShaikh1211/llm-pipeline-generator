from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from saturn_agent.persistence import JobRecord, JobRepository

from saturn_api.auth import CurrentUser, get_current_user
from saturn_api.deps import get_job_service, get_repository
from saturn_api.schemas.jobs import (
    CreateJobRequest,
    JobEventResponse,
    JobListResponse,
    JobResponse,
)
from saturn_api.services.event_stream import event_stream
from saturn_api.services.job_service import JobService


router = APIRouter()


@router.post("/api/jobs", response_model=JobResponse, status_code=201)
def create_job(
    body: CreateJobRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[JobService, Depends(get_job_service)],
    repository: Annotated[JobRepository, Depends(get_repository)],
) -> JobResponse:
    try:
        job_id = service.create_and_dispatch(
            user_id=user.user_id,
            spec=body.spec,
            target_repo=body.target_repo,
            devbox_host=body.devbox_host,
            mock_script_path=body.mock_script_path,
            conventions_path=body.conventions_path,
            target_repo_source=body.target_repo_source,
            target_repo_branch=body.target_repo_branch,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    record = repository.get_job(job_id)
    assert record is not None
    return _to_response(record)


@router.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    repository: Annotated[JobRepository, Depends(get_repository)],
) -> JobResponse:
    record = repository.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    if record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return _to_response(record)


@router.get("/api/jobs", response_model=JobListResponse)
def list_jobs(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    repository: Annotated[JobRepository, Depends(get_repository)],
) -> JobListResponse:
    # InMemoryJobRepository doesn't expose list-all in the Protocol; for v1 we
    # pull via the underlying dict. When SqlAlchemy lands, swap for a real query.
    all_jobs: list[JobRecord] = list(getattr(repository, "_jobs", {}).values())
    mine = sorted(
        (j for j in all_jobs if j.user_id == user.user_id),
        key=lambda j: j.created_at,
        reverse=True,
    )
    return JobListResponse(jobs=[_to_response(j) for j in mine])


@router.get("/api/jobs/{job_id}/events")
async def stream_events(
    job_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    repository: Annotated[JobRepository, Depends(get_repository)],
) -> EventSourceResponse:
    record = repository.get_job(job_id)
    if record is None or record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return EventSourceResponse(event_stream(repository, job_id))


@router.get(
    "/api/jobs/{job_id}/events/replay",
    response_model=list[JobEventResponse],
)
def replay_events(
    job_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    repository: Annotated[JobRepository, Depends(get_repository)],
) -> list[JobEventResponse]:
    """Plain JSON replay of recorded events (no streaming). Useful for tests
    and for clients that don't want to hold an SSE connection open."""
    record = repository.get_job(job_id)
    if record is None or record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return [
        JobEventResponse(
            job_id=e.job_id,
            stage=e.stage,
            event_type=e.event_type,
            payload=e.payload,
            created_at=e.created_at,
        )
        for e in repository.list_events(job_id)
    ]


def _to_response(record: JobRecord) -> JobResponse:
    return JobResponse(
        job_id=record.job_id,
        user_id=record.user_id,
        target_repo=record.target_repo,
        stage=record.stage,
        spec=record.spec,
        plan=record.plan,
        error=record.error,
        open_questions=list(record.open_questions),
        tokens_used=record.tokens_used,
        workspace_path=record.workspace_path,
        devbox_host=record.devbox_host,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finished_at=record.finished_at,
    )
