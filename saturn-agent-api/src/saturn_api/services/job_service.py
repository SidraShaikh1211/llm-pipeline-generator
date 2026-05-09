from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from saturn_agent.agent.claude_client import ClaudeClient
from saturn_agent.agent.client import (
    FakeLLMClient,
    GeminiClient,
    LLMClient,
    load_fake_client_from_script,
)
from saturn_agent.agent.prompt_builder import PromptBuilder
from saturn_agent.agent.token_budget import TokenBudget
from saturn_agent.config import get_settings as get_worker_settings
from saturn_agent.orchestrator import (
    JobContext,
    Orchestrator,
    OrchestratorDeps,
    default_handlers,
)
from saturn_agent.persistence import JobRepository
from saturn_agent.tools import builtin_registry

from saturn_api.config import Settings


log = logging.getLogger(__name__)


class JobService:
    """Creates jobs, dispatches them to a thread pool, returns the JobRecord.

    Synchronous-looking from the request handler's POV, but the orchestrator
    runs in a background thread so POST /api/jobs returns immediately while
    SSE clients can stream events.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        repository: JobRepository,
        executor: ThreadPoolExecutor,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.executor = executor

    def create_and_dispatch(
        self,
        *,
        user_id: str,
        spec: dict[str, Any],
        target_repo: str,
        devbox_host: str | None = None,
        mock_script_path: str | None = None,
        conventions_path: str | None = None,
        target_repo_source: str | None = None,
        target_repo_branch: str | None = None,
    ) -> uuid.UUID:
        job_id = uuid.uuid4()
        workspace_path = self._workspace_for(job_id)
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Inject repo-clone fields into the spec so stage_init can read them
        # without changing the JobContext signature.
        merged_spec = dict(spec)
        if target_repo_source:
            merged_spec.setdefault("target_repo_source", target_repo_source)
        if target_repo_branch:
            merged_spec.setdefault("target_repo_branch", target_repo_branch)

        ctx = JobContext(
            job_id=job_id,
            user_id=user_id,
            spec=merged_spec,
            target_repo=target_repo,
            devbox_host=devbox_host,
            workspace_path=workspace_path,
        )

        deps = self._build_deps(
            mock_script_path=mock_script_path,
            conventions_path=conventions_path,
        )

        # Pre-create the job record synchronously so the API can serve
        # GET /api/jobs/{id} immediately, even before the orchestrator starts.
        # Emit job.created here too: the orchestrator otherwise only emits it
        # when it also creates the record, which we've now bypassed.
        self.repository.create_job(
            job_id=ctx.job_id,
            user_id=ctx.user_id,
            spec=ctx.spec,
            target_repo=ctx.target_repo,
            workspace_path=str(ctx.workspace_path),
            devbox_host=ctx.devbox_host,
            stage="init",
        )
        self.repository.append_event(
            job_id=ctx.job_id,
            stage="init",
            event_type="job.created",
            payload={"target_repo": ctx.target_repo},
        )

        self.executor.submit(self._run, ctx, deps)
        return job_id

    def _run(self, ctx: JobContext, deps: OrchestratorDeps) -> None:
        try:
            Orchestrator(ctx, deps, handlers=default_handlers()).run()
        except Exception:
            log.exception("orchestrator crashed for job %s", ctx.job_id)

    def _workspace_for(self, job_id: uuid.UUID) -> Path:
        return self.settings.workspace_root / str(job_id)

    def _build_deps(
        self,
        *,
        mock_script_path: str | None,
        conventions_path: str | None,
    ) -> OrchestratorDeps:
        skills_root = self.settings.skills_root or _locate_default_skills_root()
        if not skills_root.is_dir():
            raise RuntimeError(
                f"skills_root does not exist: {skills_root}. "
                f"Set SATURN_API_SKILLS_ROOT or install saturn-agent-worker."
            )

        llm_client = self._build_llm_client(mock_script_path)

        conv_path: Path | None = None
        if conventions_path:
            conv_path = Path(conventions_path)
            if not conv_path.is_file():
                raise RuntimeError(f"conventions file not found: {conv_path}")

        return OrchestratorDeps(
            llm_client=llm_client,
            registry=builtin_registry(),
            budget=TokenBudget(max_total=self.settings.max_tokens_per_job),
            prompt_builder=PromptBuilder(),
            skills_root=skills_root,
            references_root=skills_root / "references",
            conventions_path=conv_path,
            repository=self.repository,
        )

    def _build_llm_client(self, mock_script_path: str | None) -> LLMClient:
        # Precedence: per-request mock > default_mock_script > configured provider.
        path = mock_script_path or (
            str(self.settings.default_mock_script_path)
            if self.settings.default_mock_script_path
            else None
        )
        if path:
            mock_path = Path(path)
            if not mock_path.is_file():
                raise RuntimeError(f"mock script not found: {mock_path}")
            return load_fake_client_from_script(mock_path)

        provider = (self.settings.llm_provider or "").lower()
        worker_settings = get_worker_settings()
        if provider == "claude":
            return ClaudeClient(worker_settings)
        if provider == "gemini":
            return GeminiClient(worker_settings)
        if provider == "mock":
            raise RuntimeError(
                "llm_provider is 'mock' — every job must supply mock_script_path"
            )
        raise RuntimeError(
            f"unknown llm_provider {provider!r}; set SATURN_API_LLM_PROVIDER to "
            f"'claude', 'gemini', or 'mock'"
        )


def _locate_default_skills_root() -> Path:
    """Best-effort: find the worker's bundled skills/ folder via the package layout."""
    import saturn_agent

    pkg_root = Path(saturn_agent.__file__).resolve().parent
    # src/saturn_agent → repo root → skills
    candidate = pkg_root.parent.parent / "skills"
    return candidate
