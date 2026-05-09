from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from saturn_agent.agent.client import LLMClient
from saturn_agent.agent.event_recorder import EventRecorder
from saturn_agent.agent.prompt_builder import PromptBuilder
from saturn_agent.agent.token_budget import TokenBudget
from saturn_agent.agent.tool_loop import AgentToolLoop
from saturn_agent.persistence.repository import InMemoryJobRepository, JobRepository
from saturn_agent.tools.base import ToolContext, ToolRegistry
from saturn_agent.workspace.repo_clone import RepoCloneService


# Stage-scoped tool sets. Tightening these is a security layer (LLD §1.5):
# the planner literally cannot call write_file because the spec is excluded
# from its tool list.
PLANNING_TOOLS: tuple[str, ...] = (
    "read_file",
    "list_dir",
    "grep",
    "find_sibling",
    "read_skill",
    "read_reference",
    "read_conventions",
)

GENERATION_TOOLS: tuple[str, ...] = (
    *PLANNING_TOOLS,
    "write_file",
    "scaffold_test_fixture",
)

VALIDATION_TOOLS: tuple[str, ...] = (
    "scalafmt",
    "scalac",
    "convention_check",
)

TESTING_TOOLS: tuple[str, ...] = (
    "run_local_tests",
    "run_devbox_tests",
)

REPAIR_TOOLS: tuple[str, ...] = GENERATION_TOOLS


AgentFactory = Callable[[], AgentToolLoop]


@dataclass
class OrchestratorDeps:
    """All injected dependencies a stage handler needs.

    Build this once per job; pass the same instance to every stage.
    """

    llm_client: LLMClient
    registry: ToolRegistry
    budget: TokenBudget
    prompt_builder: PromptBuilder
    skills_root: Path
    references_root: Path
    conventions_path: Path | None = None
    repository: JobRepository = field(default_factory=InMemoryJobRepository)
    repo_clone_service: RepoCloneService = field(default_factory=RepoCloneService)

    def agent_loop(
        self, *, recorder: EventRecorder | None = None
    ) -> AgentToolLoop:
        return AgentToolLoop(
            self.llm_client, self.registry, self.budget, recorder=recorder
        )

    def tool_ctx(self, ctx: Any) -> ToolContext:
        return ToolContext(
            job_id=str(ctx.job_id),
            workspace_path=ctx.workspace_path,
            target_repo_path=ctx.workspace_path / "repos" / ctx.target_repo,
            devbox_host=ctx.devbox_host,
            user_id=ctx.user_id,
            skills_root=self.skills_root,
            references_root=self.references_root,
            conventions_path=self.conventions_path,
        )
