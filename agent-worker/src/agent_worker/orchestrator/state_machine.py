from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from agent_worker.observability.logging import (
    bind_job_context,
    bind_stage,
    get_logger,
    unbind_stage,
)

log = get_logger(__name__)


class Stage(str, Enum):
    INIT = "init"
    PLANNING = "planning"
    OPTIMIZING_PLAN = "optimizing_plan"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    TESTING = "testing"
    COMMITTING = "committing"
    OPENING_PR = "opening_pr"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PAUSED_FOR_USER = "paused_for_user"


TERMINAL_STAGES: frozenset[Stage] = frozenset(
    {Stage.SUCCEEDED, Stage.FAILED, Stage.PAUSED_FOR_USER}
)


@dataclass
class JobContext:
    spec: dict[str, Any]
    target_repo: str
    workspace_path: Path
    user_id: str
    job_id: uuid.UUID = field(default_factory=uuid.uuid4)
    devbox_host: str | None = None

    plan: dict[str, Any] | None = None
    generated_files: list[str] = field(default_factory=list)
    generated_files_content: dict[str, str] = field(default_factory=dict)
    validation_attempts: int = 0
    test_attempts: int = 0
    pr_url: str | None = None
    error: str | None = None
    open_questions: list[str] = field(default_factory=list)
    tokens_used: int = 0

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


StageHandler = Callable[[JobContext, "OrchestratorDeps"], Stage]


class Orchestrator:
    """Drives a single job through stages.

    The state machine is dumb on purpose: a dict of handlers, dispatched by the
    current stage. The LLM is invoked inside handlers, never from here.
    """

    MAX_VALIDATION_ATTEMPTS: int = 3
    MAX_TEST_ATTEMPTS: int = 2

    def __init__(
        self,
        ctx: JobContext,
        deps: "OrchestratorDeps",
        *,
        handlers: dict[Stage, StageHandler] | None = None,
    ) -> None:
        self.ctx = ctx
        self.deps = deps
        self.handlers: dict[Stage, StageHandler] = handlers or {}

    def run(self) -> Stage:
        bind_job_context(job_id=str(self.ctx.job_id), user_id=self.ctx.user_id)

        repository = self.deps.repository
        if repository.get_job(self.ctx.job_id) is None:
            repository.create_job(
                job_id=self.ctx.job_id,
                user_id=self.ctx.user_id,
                spec=self.ctx.spec,
                target_repo=self.ctx.target_repo,
                workspace_path=str(self.ctx.workspace_path),
                devbox_host=self.ctx.devbox_host,
                stage=Stage.INIT.value,
            )
            repository.append_event(
                job_id=self.ctx.job_id,
                stage=Stage.INIT.value,
                event_type="job.created",
                payload={"target_repo": self.ctx.target_repo},
            )
            log.info(
                "job.created",
                target_repo=self.ctx.target_repo,
                workspace=str(self.ctx.workspace_path),
            )

        stage = Stage.INIT
        while stage not in TERMINAL_STAGES:
            bind_stage(stage.value)
            entered_at = datetime.now(UTC)
            repository.append_event(
                job_id=self.ctx.job_id,
                stage=stage.value,
                event_type="stage.entered",
                payload={},
            )
            log.info("stage.entered")

            handler = self.handlers.get(stage)
            if handler is None:
                self.ctx.error = (
                    self.ctx.error
                    or f"no handler registered for stage {stage.value!r}"
                )
                next_stage = Stage.FAILED
            else:
                next_stage = handler(self.ctx, self.deps)

            duration_ms = int(
                (datetime.now(UTC) - entered_at).total_seconds() * 1000
            )
            repository.append_event(
                job_id=self.ctx.job_id,
                stage=stage.value,
                event_type="stage.exited",
                payload={
                    "next_stage": next_stage.value,
                    "duration_ms": duration_ms,
                    "error": self.ctx.error,
                },
            )
            repository.update_job(
                self.ctx.job_id,
                stage=next_stage.value,
                plan=self.ctx.plan,
                error=self.ctx.error,
                open_questions=self.ctx.open_questions,
                tokens_used=self.ctx.tokens_used,
            )
            log.info(
                "stage.exited",
                next_stage=next_stage.value,
                duration_ms=duration_ms,
                error=self.ctx.error,
            )
            stage = next_stage

        unbind_stage()
        terminal_event = {
            Stage.SUCCEEDED: "job.succeeded",
            Stage.FAILED: "job.failed",
            Stage.PAUSED_FOR_USER: "job.paused",
        }.get(stage, "job.terminal")
        repository.update_job(
            self.ctx.job_id,
            stage=stage.value,
            error=self.ctx.error,
            tokens_used=self.ctx.tokens_used,
            finished_at=datetime.now(UTC),
        )
        repository.append_event(
            job_id=self.ctx.job_id,
            stage=stage.value,
            event_type=terminal_event,
            payload={"tokens_used": self.ctx.tokens_used, "error": self.ctx.error},
        )
        log.info(terminal_event, tokens_used=self.ctx.tokens_used)
        return stage


# Forward import to satisfy type checkers; the real OrchestratorDeps lives in deps.py.
from agent_worker.orchestrator.deps import OrchestratorDeps  # noqa: E402
