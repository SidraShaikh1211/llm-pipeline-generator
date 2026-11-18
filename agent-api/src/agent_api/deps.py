from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

from fastapi import Depends, Request

from agent_worker.persistence import InMemoryJobRepository, JobRepository

from agent_api.config import Settings, get_settings
from agent_api.services.job_service import JobService


def get_repository(request: Request) -> JobRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


def get_executor(request: Request) -> ThreadPoolExecutor:
    return request.app.state.executor  # type: ignore[no-any-return]


def get_job_service(
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[JobRepository, Depends(get_repository)],
    executor: Annotated[ThreadPoolExecutor, Depends(get_executor)],
) -> JobService:
    return JobService(settings=settings, repository=repository, executor=executor)


__all__ = [
    "get_executor",
    "get_job_service",
    "get_repository",
    "get_settings",
    "InMemoryJobRepository",
]
