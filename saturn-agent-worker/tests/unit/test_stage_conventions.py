from __future__ import annotations

import json
from pathlib import Path

from saturn_agent.agent.client import FakeLLMClient
from saturn_agent.agent.prompt_builder import PromptBuilder
from saturn_agent.agent.token_budget import TokenBudget
from saturn_agent.agent.types import GenerateResult
from saturn_agent.orchestrator import (
    JobContext,
    OrchestratorDeps,
    Stage,
)
from saturn_agent.orchestrator.stages import stage_planning
from saturn_agent.tools import builtin_registry


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
REFERENCES_ROOT = SKILLS_ROOT / "references"


_PLAN_JSON = (
    '{"pipeline_name": "foo_reports", "target_repo": "saturn-pipelines-reports", '
    '"files": [{"path": "FooNode.scala", "kind": "scala_node", '
    '"depends_on": [], "description": "stub"}]}'
)


def _write_conventions(path: Path, pipeline_name: str = "foo_reports") -> None:
    path.write_text(
        json.dumps(
            {
                "repo_name": "saturn-pipelines-reports",
                "extracted_at": "2026-05-01T00:00:00Z",
                "common_imports": ["import saturn.framework.Reference"],
                "pipelines": [
                    {
                        "pipeline_name": pipeline_name,
                        "folder_path": pipeline_name,
                        "provider": None,
                        "nodes": [],
                        "domain": None,
                    },
                    {
                        "pipeline_name": "other_pipeline",
                        "folder_path": "other_pipeline",
                        "provider": None,
                        "nodes": [],
                        "domain": None,
                    },
                ],
            }
        )
    )


def _build_deps(workspace: Path, conventions_path: Path | None) -> tuple[OrchestratorDeps, FakeLLMClient]:
    client = FakeLLMClient([GenerateResult(text=_PLAN_JSON, tokens_used=10)])
    deps = OrchestratorDeps(
        llm_client=client,
        registry=builtin_registry(),
        budget=TokenBudget(max_total=10_000),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=REFERENCES_ROOT,
        conventions_path=conventions_path,
    )
    return deps, client


def test_planning_includes_conventions_when_path_set(
    workspace: Path, tmp_path: Path
) -> None:
    conv_path = tmp_path / "c.json"
    _write_conventions(conv_path)
    deps, client = _build_deps(workspace, conv_path)
    ctx = JobContext(
        spec={"pipeline_name": "foo_reports", "target_repo": "saturn-pipelines-reports"},
        target_repo="saturn-pipelines-reports",
        workspace_path=workspace,
        user_id="u",
    )

    next_stage = stage_planning(ctx, deps)

    assert next_stage == Stage.OPTIMIZING_PLAN
    sent_prompt = client.calls[0]["system_prompt"]
    assert "Conventions slice" in sent_prompt
    # the slice should contain the matching pipeline...
    assert "foo_reports" in sent_prompt
    # ...and NOT the unrelated pipeline (slicing actually happened)
    assert "other_pipeline" not in sent_prompt


def test_planning_omits_conventions_when_path_none(workspace: Path) -> None:
    deps, client = _build_deps(workspace, conventions_path=None)
    ctx = JobContext(
        spec={"pipeline_name": "foo_reports", "target_repo": "saturn-pipelines-reports"},
        target_repo="saturn-pipelines-reports",
        workspace_path=workspace,
        user_id="u",
    )
    stage_planning(ctx, deps)
    sent_prompt = client.calls[0]["system_prompt"]
    assert "Conventions slice" not in sent_prompt


def test_planning_uses_full_conventions_when_pipeline_unknown(
    workspace: Path, tmp_path: Path
) -> None:
    conv_path = tmp_path / "c.json"
    _write_conventions(conv_path, pipeline_name="some_existing_pipeline")
    deps, client = _build_deps(workspace, conv_path)
    ctx = JobContext(
        spec={"pipeline_name": "brand_new_pipeline", "target_repo": "saturn-pipelines-reports"},
        target_repo="saturn-pipelines-reports",
        workspace_path=workspace,
        user_id="u",
    )
    stage_planning(ctx, deps)
    sent_prompt = client.calls[0]["system_prompt"]
    # No exact match → fall back to full repo conventions, both pipelines visible
    assert "some_existing_pipeline" in sent_prompt
    assert "other_pipeline" in sent_prompt
