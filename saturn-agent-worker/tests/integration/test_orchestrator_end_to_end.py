from __future__ import annotations

import json
from pathlib import Path

from saturn_agent.agent.client import FakeLLMClient
from saturn_agent.agent.prompt_builder import PromptBuilder
from saturn_agent.agent.token_budget import TokenBudget
from saturn_agent.agent.types import GenerateResult, ToolCall
from saturn_agent.orchestrator import (
    JobContext,
    Orchestrator,
    OrchestratorDeps,
    Stage,
    default_handlers,
)
from saturn_agent.tools import builtin_registry


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
REFERENCES_ROOT = SKILLS_ROOT / "references"


_PLAN_JSON = json.dumps(
    {
        "pipeline_name": "foo_reports",
        "target_repo": "saturn-pipelines-reports",
        "skills_required": ["saturn-pipeline-builder"],
        "files": [
            {
                "path": "FooNode.scala",
                "kind": "scala_node",
                "style": "a_single_node",
                "role": "leaf_transform",
                "depends_on": [],
                "description": "Stub Foo node — for the e2e harness only.",
            },
            {
                "path": "FooRocketProvider.scala",
                "kind": "scala_provider",
                "depends_on": ["FooNode.scala"],
                "description": "Provider chain that registers FooNode and calls .finish().",
            },
        ],
        "ddls": [],
        "assumptions": [],
        "open_questions": [],
        "estimated_tokens": 0,
    }
)


# Self-contained Scala fixtures: scalac runs against these directly (no
# Saturn library jars on the classpath), so the bodies must not reference
# anything outside stdlib. The provider file still contains `.finish()` to
# satisfy the `provider_must_call_finish` convention rule, which scans for
# the literal call regardless of the receiver type.
_FOO_NODE_SCALA = (
    'package saturn.foo\n'
    'class FooNode {\n'
    '  val name: String = "foo"\n'
    '}\n'
)


_FOO_PROVIDER_SCALA = (
    'package saturn.foo\n'
    '\n'
    'object FooRocketProvider {\n'
    '  private object Builder {\n'
    '    def add(name: String): Builder.type = this\n'
    '    def finish(): Unit = ()\n'
    '  }\n'
    '  val started: Unit = Builder.add("foo_workflow").finish()\n'
    '}\n'
)


_SCALAFMT_CONF = (
    'version = "3.11.0"\n'
    'runner.dialect = scala3\n'
)


def _build_deps(conventions_path: Path | None = None) -> OrchestratorDeps:
    responses = [
        # Planning: emit final plan immediately, no tool calls.
        GenerateResult(text=_PLAN_JSON, tokens_used=200),
        # Generation 1 (FooNode.scala): one write_file call, then "done".
        GenerateResult(
            text=None,
            tool_calls=[
                ToolCall(
                    id="w1",
                    name="write_file",
                    arguments={"path": "FooNode.scala", "content": _FOO_NODE_SCALA},
                )
            ],
            tokens_used=80,
        ),
        GenerateResult(text="wrote FooNode", tokens_used=10),
        # Generation 2 (FooRocketProvider.scala): same pattern.
        GenerateResult(
            text=None,
            tool_calls=[
                ToolCall(
                    id="w2",
                    name="write_file",
                    arguments={"path": "FooRocketProvider.scala", "content": _FOO_PROVIDER_SCALA},
                )
            ],
            tokens_used=80,
        ),
        GenerateResult(text="wrote provider", tokens_used=10),
    ]

    return OrchestratorDeps(
        llm_client=FakeLLMClient(responses),
        registry=builtin_registry(),
        budget=TokenBudget(max_total=10_000),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=REFERENCES_ROOT,
        conventions_path=conventions_path,
    )


def _job_ctx(workspace: Path) -> JobContext:
    return JobContext(
        spec={
            "pipeline_name": "foo_reports",
            "target_repo": "saturn-pipelines-reports",
            "transformations": "Stub for e2e",
        },
        target_repo="saturn-pipelines-reports",
        workspace_path=workspace,
        user_id="local-dev",
    )


def test_end_to_end_succeeds_without_real_llm(workspace: Path) -> None:
    # Plant a workspace-level scalafmt config so the format step can run
    # without a cloned target repo. Production picks this up via
    # `_scalafmt_cwd`'s workspace fallback.
    (workspace / ".scalafmt.conf").write_text(_SCALAFMT_CONF, encoding="utf-8")

    deps = _build_deps()
    ctx = _job_ctx(workspace)

    final = Orchestrator(ctx, deps, handlers=default_handlers()).run()

    assert final == Stage.SUCCEEDED, f"unexpected error: {ctx.error}"
    assert ctx.error is None
    assert (workspace / "FooNode.scala").is_file()
    assert (workspace / "FooRocketProvider.scala").is_file()
    assert ctx.generated_files == ["FooNode.scala", "FooRocketProvider.scala"]
    # Planning + 2 generations × 2 calls each = 5 LLM calls
    assert deps.budget.used == 200 + 80 + 10 + 80 + 10

    events = deps.repository.list_events(ctx.job_id)
    types = [e.event_type for e in events]
    # 5 LLM calls scripted → 5 llm.called events
    assert types.count("llm.called") == 5
    # 2 write_file invocations scripted → 2 tool.invoked events
    assert types.count("tool.invoked") == 2
    tool_events = [e for e in events if e.event_type == "tool.invoked"]
    assert all(e.payload["tool"] == "write_file" for e in tool_events)
    assert all(e.payload["ok"] is True for e in tool_events)


def test_planning_pause_when_open_questions(workspace: Path) -> None:
    plan_with_qs = json.dumps(
        {
            "pipeline_name": "foo_reports",
            "target_repo": "saturn-pipelines-reports",
            "skills_required": [],
            "files": [],
            "ddls": [],
            "assumptions": [],
            "open_questions": ["What is the target schema?"],
            "estimated_tokens": 0,
        }
    )
    deps = OrchestratorDeps(
        llm_client=FakeLLMClient([GenerateResult(text=plan_with_qs, tokens_used=10)]),
        registry=builtin_registry(),
        budget=TokenBudget(max_total=10_000),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=REFERENCES_ROOT,
    )
    ctx = _job_ctx(workspace)
    final = Orchestrator(ctx, deps, handlers=default_handlers()).run()
    assert final == Stage.PAUSED_FOR_USER
    assert ctx.open_questions == ["What is the target schema?"]


def test_planning_fails_on_malformed_plan(workspace: Path) -> None:
    deps = OrchestratorDeps(
        llm_client=FakeLLMClient(
            [GenerateResult(text="this is not json", tokens_used=10)]
        ),
        registry=builtin_registry(),
        budget=TokenBudget(max_total=10_000),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=REFERENCES_ROOT,
    )
    ctx = _job_ctx(workspace)
    final = Orchestrator(ctx, deps, handlers=default_handlers()).run()
    assert final == Stage.FAILED
    assert "plan parse error" in (ctx.error or "")


def test_validation_failure_triggers_repair_then_fail(workspace: Path) -> None:
    """Planner OK, generator writes file with `println`, repair tries again
    but writes the same bad content; orchestrator gives up after MAX_VALIDATION_ATTEMPTS."""
    plan = json.dumps(
        {
            "pipeline_name": "x",
            "target_repo": "saturn-pipelines-reports",
            "files": [
                {
                    "path": "Bad.scala",
                    "kind": "scala_node",
                    "depends_on": [],
                    "description": "intentionally bad",
                }
            ],
        }
    )
    bad_scala = 'class Bad { println("oops") }\n'
    write_call = ToolCall(
        id="w",
        name="write_file",
        arguments={"path": "Bad.scala", "content": bad_scala},
    )

    # Generation: 1 tool call + 1 final per attempt; repair: same; +final no-op.
    # We loop validation/repair MAX_VALIDATION_ATTEMPTS=3 times before failing.
    responses = [GenerateResult(text=plan, tokens_used=10)]
    for _ in range(4):  # generation + 3 repair attempts
        responses.append(GenerateResult(text=None, tool_calls=[write_call], tokens_used=5))
        responses.append(GenerateResult(text="done", tokens_used=2))

    deps = OrchestratorDeps(
        llm_client=FakeLLMClient(responses),
        registry=builtin_registry(),
        budget=TokenBudget(max_total=100_000),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=REFERENCES_ROOT,
    )
    ctx = _job_ctx(workspace)
    final = Orchestrator(ctx, deps, handlers=default_handlers()).run()

    assert final == Stage.FAILED
    assert ctx.validation_attempts == Orchestrator.MAX_VALIDATION_ATTEMPTS
    assert "no_println" in (ctx.error or "")


def test_init_fails_on_missing_workspace(tmp_path: Path) -> None:
    deps = OrchestratorDeps(
        llm_client=FakeLLMClient([]),
        registry=builtin_registry(),
        budget=TokenBudget(max_total=10),
        prompt_builder=PromptBuilder(),
        skills_root=SKILLS_ROOT,
        references_root=REFERENCES_ROOT,
    )
    ctx = JobContext(
        spec={},
        target_repo="x",
        workspace_path=tmp_path / "does_not_exist",
        user_id="u",
    )
    final = Orchestrator(ctx, deps, handlers=default_handlers()).run()
    assert final == Stage.FAILED
    assert "workspace does not exist" in (ctx.error or "")
