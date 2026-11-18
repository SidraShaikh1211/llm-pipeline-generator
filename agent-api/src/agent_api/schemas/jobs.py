from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    spec: dict[str, Any]
    target_repo: str
    devbox_host: str | None = None
    mock_script_path: str | None = Field(
        default=None,
        description=(
            "Server-local path to a JSON file with scripted LLM responses. "
            "When set, the agent uses FakeLLMClient instead of GeminiClient."
        ),
    )
    conventions_path: str | None = None
    target_repo_source: str | None = Field(
        default=None,
        description=(
            "Git URL or local absolute path. When set, the worker clones the repo "
            "into <workspace>/repos/<target_repo>/ before planning runs."
        ),
    )
    target_repo_branch: str | None = None


class JobResponse(BaseModel):
    job_id: UUID
    user_id: str
    target_repo: str
    stage: str
    spec: dict[str, Any]
    plan: dict[str, Any] | None = None
    error: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    workspace_path: str | None = None
    devbox_host: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]


class JobEventResponse(BaseModel):
    job_id: UUID
    stage: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
