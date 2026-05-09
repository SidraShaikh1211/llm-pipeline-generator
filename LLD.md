# Saturn Agent — Low-Level Design

This document is the implementation plan. It is structured for Claude Code to read and execute against, repo by repo, module by module.

Conventions used in this doc:
- File paths are relative to the repo root unless noted.
- Python: 3.11+, type-checked with mypy strict, formatted with ruff.
- Frontend: TypeScript, React 18, Vite, Tailwind, TanStack Query.
- All persistence schemas use Postgres 15+; migrations via Alembic.

---

## 1. Repo: `saturn-agent-worker`

The single most important repo. Contains the orchestrator, agent, and all tools. Everything else exists to serve this.

### 1.1 Top-level layout

```
saturn-agent-worker/
├── pyproject.toml
├── README.md
├── src/
│   └── saturn_agent/
│       ├── __init__.py
│       ├── cli.py                    # entrypoint for local runs
│       ├── config.py                 # pydantic settings
│       ├── orchestrator/
│       │   ├── __init__.py
│       │   ├── state_machine.py      # FSM definition
│       │   ├── stages.py             # stage handlers (plan, generate, validate, test, pr)
│       │   ├── plan.py               # plan dataclass + validation
│       │   └── repair.py             # repair loop logic
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── client.py             # Gemini API wrapper
│       │   ├── prompt_builder.py     # composes system prompt from skill + refs
│       │   ├── tool_loop.py          # tool-calling loop
│       │   └── token_budget.py       # tracks per-job spend
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── base.py               # Tool ABC, registry
│       │   ├── repo_nav.py           # read_file, list_dir, grep, find_sibling
│       │   ├── codebase.py           # read_skill, read_reference, read_conventions
│       │   ├── emit.py               # write_file, scaffold_test_fixture
│       │   ├── validate.py           # scalac, scalafmt, convention_check
│       │   ├── test_local.py         # local test runner
│       │   ├── test_devbox.py        # SSH-based test runner
│       │   ├── vcs.py                # git operations
│       │   └── pr.py                 # bitbucket API client
│       ├── workspace/
│       │   ├── __init__.py
│       │   ├── manager.py            # workspace lifecycle
│       │   └── repo_clone.py         # clone/refresh logic
│       ├── conventions/
│       │   ├── __init__.py
│       │   ├── loader.py             # reads conventions JSON from MinIO
│       │   └── selector.py           # picks relevant slice for current job
│       ├── persistence/
│       │   ├── __init__.py
│       │   ├── models.py             # SQLAlchemy ORM
│       │   ├── repository.py         # data-access layer
│       │   └── migrations/           # alembic
│       ├── observability/
│       │   ├── __init__.py
│       │   ├── logging.py            # structured logging
│       │   ├── metrics.py            # prometheus
│       │   └── tracing.py            # opentelemetry
│       └── tasks/
│           ├── __init__.py
│           └── celery_app.py         # celery worker entrypoint
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── skills/                           # ← copy of saturn-skills/ folder
│   ├── saturn-pipeline-builder/SKILL.md
│   ├── saturn-ddl-builder/SKILL.md
│   ├── saturn-pipeline-tester/SKILL.md
│   └── references/
├── deployment/
│   ├── Dockerfile
│   ├── k8s/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── secrets.example.yaml
│   └── helm/
│       └── values.yaml
└── scripts/
    ├── run_local.sh
    └── seed_eval.sh
```

### 1.2 `config.py` — settings

```python
# src/saturn_agent/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SATURN_AGENT_", env_file=".env")

    # LLM
    gemini_api_url: str
    gemini_api_key: SecretStr
    gemini_model: str = "gemini-2.5-pro"          # match what your internal Gemini exposes
    gemini_temperature: float = 0.1
    gemini_max_output_tokens: int = 8192

    # Workspace
    workspace_root: str = "/workspace"
    workspace_ttl_days: int = 7

    # Token budget
    max_tokens_per_job: int = 500_000

    # Persistence
    postgres_dsn: str
    redis_url: str
    minio_endpoint: str
    minio_access_key: SecretStr
    minio_secret_key: SecretStr
    minio_bucket: str = "saturn-agent"

    # Bitbucket
    bitbucket_base_url: str
    bitbucket_service_account_token: SecretStr
    bitbucket_default_target_branch: str = "develop"

    # Observability
    otlp_endpoint: str | None = None
    log_level: str = "INFO"

settings = Settings()
```

All env-driven. No hardcoded paths or URLs anywhere else in the codebase.

### 1.3 Orchestrator — the state machine

This is the heart of the deterministic side. Implementation is a small explicit FSM, not a workflow library.

```python
# src/saturn_agent/orchestrator/state_machine.py

from enum import Enum
from dataclasses import dataclass, field
from typing import Any
import uuid
from datetime import datetime, UTC

class Stage(str, Enum):
    INIT = "init"
    PLANNING = "planning"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    TESTING = "testing"
    COMMITTING = "committing"
    OPENING_PR = "opening_pr"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PAUSED_FOR_USER = "paused_for_user"

@dataclass
class JobContext:
    job_id: uuid.UUID
    user_id: str
    spec: dict[str, Any]
    target_repo: str
    workspace_path: str
    devbox_host: str | None
    plan: dict[str, Any] | None = None
    generated_files: list[str] = field(default_factory=list)
    validation_attempts: int = 0
    test_attempts: int = 0
    pr_url: str | None = None
    error: str | None = None
    tokens_used: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

class Orchestrator:
    """
    Drives a single job through stages. Persists state at every transition.
    The LLM is invoked inside stage handlers — never from the orchestrator itself.
    """

    MAX_VALIDATION_ATTEMPTS = 3
    MAX_TEST_ATTEMPTS = 2

    def __init__(self, ctx: JobContext, deps: "OrchestratorDeps"):
        self.ctx = ctx
        self.deps = deps  # injected: repo, agent, tool_registry, workspace_mgr, ...

    def run(self) -> Stage:
        stage = Stage.INIT
        while stage not in (Stage.SUCCEEDED, Stage.FAILED, Stage.PAUSED_FOR_USER):
            self._persist_stage(stage)
            stage = self._handle(stage)
        self._persist_stage(stage)
        return stage

    def _handle(self, stage: Stage) -> Stage:
        match stage:
            case Stage.INIT:        return self._init()
            case Stage.PLANNING:    return self._plan()
            case Stage.GENERATING:  return self._generate()
            case Stage.VALIDATING:  return self._validate()
            case Stage.REPAIRING:   return self._repair()
            case Stage.TESTING:     return self._test()
            case Stage.COMMITTING:  return self._commit()
            case Stage.OPENING_PR:  return self._open_pr()
            case _: raise RuntimeError(f"unhandled stage {stage}")

    # one method per stage, each returns the next stage
    # all real logic delegates to stages.py — keep this file as a router
```

Stage handlers (`stages.py`) are pure-ish: take `JobContext`, return next `Stage`, mutate the context. Each handler emits a metrics event and a structured log line on entry/exit.

**Design principle**: the orchestrator never calls Gemini. It calls *stage handlers* that may call the agent, which calls Gemini. Three layers, separated for testability — you mock the agent in orchestrator tests, mock Gemini in agent tests.

### 1.4 Plan dataclass

The output of the planning stage. This is what the rest of the orchestrator runs against.

```python
# src/saturn_agent/orchestrator/plan.py

from pydantic import BaseModel, Field
from enum import Enum

class NodeStyle(str, Enum):
    A_SINGLE_NODE = "a_single_node"
    B_TYPED_UPSTREAM = "b_typed_upstream"
    C_REFERENCE_SOURCE = "c_reference_source"

class NodeRole(str, Enum):
    SOURCE = "source"
    INTERMEDIATE_FANOUT = "intermediate_fanout"
    BUCKET = "bucket"
    LEAF_TRANSFORM = "leaf_transform"
    PERSIST = "persist"

class FilePlan(BaseModel):
    path: str  # relative to repo root
    kind: str  # "scala_node" | "scala_provider" | "scala_domain" | "ddl" | "test_spec" | "test_fixture"
    style: NodeStyle | None = None
    role: NodeRole | None = None
    depends_on: list[str] = Field(default_factory=list)  # other paths this file references
    description: str  # human-readable, surfaced in PR body

class DDLPlan(BaseModel):
    table_name: str
    schema: str
    columns: list[dict[str, str]]  # [{"name", "type", "comment"}]
    partition_cols: list[str]
    format: str
    location: str
    daily_volume_tier: int  # 0-4 from partitioning_guide.md

class Plan(BaseModel):
    pipeline_name: str
    target_repo: str
    skills_required: list[str]  # ["saturn-pipeline-builder", "saturn-ddl-builder", ...]
    files: list[FilePlan]
    ddls: list[DDLPlan] = Field(default_factory=list)
    assumptions: list[str]  # generator-stated assumptions; surfaced in PR
    open_questions: list[str]  # if non-empty, job pauses for user input
    estimated_tokens: int
```

The planner must produce this as JSON. Pydantic validates strictly. If the LLM returns malformed JSON, retry once with a stricter prompt that includes the schema; then fail.

### 1.5 Agent — the LLM caller

Single class. Wraps Gemini with the tool-calling loop.

```python
# src/saturn_agent/agent/client.py

import google.generativeai as genai  # or whatever SDK your internal Gemini uses

class GeminiClient:
    def __init__(self, settings):
        # Configure based on internal Gemini API. If it's OpenAI-compatible,
        # use openai SDK with custom base_url. If it's Google-format, use
        # google.generativeai with custom endpoint. If it's neither,
        # write a thin REST client here.
        ...

    def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
    ) -> "GenerateResult":
        ...

@dataclass
class GenerateResult:
    text: str | None
    tool_calls: list["ToolCall"]
    tokens_used: int
    finish_reason: str
```

```python
# src/saturn_agent/agent/tool_loop.py

class AgentToolLoop:
    """
    Runs a bounded tool-calling loop until the LLM returns a final answer
    (no tool_calls) or hits the iteration limit.
    """

    MAX_ITERATIONS = 25  # generous for code-gen with repo navigation

    def __init__(self, client: GeminiClient, tool_registry: ToolRegistry, budget: TokenBudget):
        self.client = client
        self.tools = tool_registry
        self.budget = budget

    def run(
        self,
        system_prompt: str,
        user_message: str,
        allowed_tools: list[str],
    ) -> "AgentResult":
        messages = [{"role": "user", "content": user_message}]
        tool_specs = [self.tools.spec(name) for name in allowed_tools]

        for iteration in range(self.MAX_ITERATIONS):
            self.budget.check()  # raises if exceeded

            result = self.client.generate(
                system_prompt=system_prompt,
                messages=messages,
                tools=tool_specs,
            )
            self.budget.add(result.tokens_used)

            if not result.tool_calls:
                return AgentResult(text=result.text, iterations=iteration + 1)

            messages.append({"role": "assistant", "content": result.text, "tool_calls": result.tool_calls})

            for call in result.tool_calls:
                output = self.tools.invoke(call.name, call.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

        raise AgentLoopExhausted(f"Hit MAX_ITERATIONS ({self.MAX_ITERATIONS})")
```

Notes:
- One `AgentToolLoop` instance per orchestrator stage. The orchestrator owns its lifecycle.
- `allowed_tools` is stage-specific. Planning stage gets `read_*` tools only; generation stage gets emit + read; validation stage gets validate + read; etc. This is a security layer — the planner cannot accidentally write a file.
- `TokenBudget` raises a typed exception when blown. The orchestrator catches and fails the job cleanly.

### 1.6 Tool surface

Each tool is a class implementing this protocol:

```python
# src/saturn_agent/tools/base.py

from typing import Protocol, Any
from pydantic import BaseModel

class ToolSpec(BaseModel):
    name: str
    description: str
    parameters_schema: dict  # JSON schema, fed to Gemini's function-calling API

class Tool(Protocol):
    spec: ToolSpec

    def invoke(self, arguments: dict, ctx: "ToolContext") -> str:
        """Returns stringified result. Raises ToolError on failure."""
        ...

class ToolContext(BaseModel):
    """Passed to every tool. Lets tools share workspace path, etc."""
    job_id: str
    workspace_path: str
    target_repo_path: str  # within workspace_path
    devbox_host: str | None
    user_id: str
```

Specific tools:

| Tool | Module | Description |
|---|---|---|
| `read_file` | `repo_nav.py` | Read file from workspace, returns content + line numbers |
| `list_dir` | `repo_nav.py` | List directory contents with file types |
| `grep` | `repo_nav.py` | Ripgrep across workspace; returns match locations |
| `find_sibling` | `repo_nav.py` | Domain-specific: given a pipeline name, finds the closest existing sibling pipeline |
| `read_skill` | `codebase.py` | Returns SKILL.md contents for one of the three skills |
| `read_reference` | `codebase.py` | Returns one of the references/*.md files |
| `read_conventions` | `codebase.py` | Returns the conventions JSON slice for a target pipeline domain |
| `write_file` | `emit.py` | Writes file to workspace; rejects writes outside workspace |
| `scaffold_test_fixture` | `emit.py` | Creates a CSV fixture file under `src/test/resources/...` |
| `scalafmt` | `validate.py` | Runs scalafmt; returns diff (or empty) |
| `scalac` | `validate.py` | Compiles against saturn classpath; returns errors |
| `convention_check` | `validate.py` | Static rules (see §1.7); returns list of violations |
| `run_local_tests` | `test_local.py` | Runs `sbt testOnly *<spec>*` for non-Spark tests |
| `run_devbox_tests` | `test_devbox.py` | SSHs to user devbox, runs Spark tests, streams output |
| `git_branch` | `vcs.py` | `git checkout -b <name>` |
| `git_commit` | `vcs.py` | Stage + commit with message |
| `git_push` | `vcs.py` | Push to origin |
| `bitbucket_open_pr` | `pr.py` | POST to Bitbucket REST API |
| `bitbucket_get_pr_status` | `pr.py` | GET PR + CI status |

Each tool is independently unit-testable (inject `ToolContext`, mock subprocess calls).

**Critical**: every tool returns plain strings (the LLM eats text). Structured errors are formatted as text the LLM can act on, e.g., `scalac` returns the actual compiler output, not a Python traceback.

### 1.7 Convention checks (deterministic validator)

The biggest single quality lever. Static rules over generated Scala that don't need an LLM to verify:

```python
# src/saturn_agent/tools/validate.py

class ConventionViolation(BaseModel):
    rule: str
    file: str
    line: int | None
    message: str

class ConventionChecker:
    """
    Runs after each generation. Catches the LLM's most common mistakes
    without an LLM round-trip.
    """

    RULES = [
        "provider_must_call_finish",
        "every_children_id_must_have_node_registration",
        "every_node_registration_must_be_referenced",
        "node_constructor_params_must_match_global_or_node_args",
        "common_case_class_must_match_consumer_reads",
        "persist_node_must_mix_in_impala_utils",
        "transform_node_must_not_mix_in_impala_utils",
        "imports_must_resolve",
        "no_simple_date_format",
        "no_println",
        "no_save_as_table",
        "intermediate_fanout_must_persist",
        "ddl_must_use_create_table_if_not_exists",
        "ddl_filename_must_match_grammar",
        "ddl_must_partition_by_business_date_id",
    ]

    def check(self, file_paths: list[str]) -> list[ConventionViolation]:
        ...
```

Implementation: parse Scala with tree-sitter, run each rule as a function over the AST. Each rule is small (10–30 lines). Adding new rules as the codebase teaches you about edge cases is cheap.

This file grows over time — every quality regression in eval becomes a new rule.

### 1.8 Workspace manager

```python
# src/saturn_agent/workspace/manager.py

class WorkspaceManager:
    def __init__(self, root: Path, repo_clone_service: "RepoCloneService"):
        self.root = root
        self.clone_svc = repo_clone_service

    def acquire(self, job_id: UUID, target_repos: list[str]) -> Path:
        path = self.root / str(job_id)
        path.mkdir(parents=True, exist_ok=False)
        for repo in target_repos:
            self.clone_svc.clone(repo, path / "repos" / repo_basename(repo))
        (path / "outputs").mkdir()
        return path

    def release(self, job_id: UUID, *, keep_for_debug: bool = False) -> None:
        path = self.root / str(job_id)
        if keep_for_debug:
            (path / ".keep").touch()
        else:
            shutil.rmtree(path)

    def cleanup_expired(self, ttl: timedelta) -> int:
        # cron task: remove workspaces older than ttl
        ...
```

`RepoCloneService` is async-friendly and cached: if the worker has cloned `saturn-pipelines-reports` before, it does `git fetch + git reset --hard origin/<branch>` instead of a full clone.

### 1.9 Stage handlers (the meaty part)

```python
# src/saturn_agent/orchestrator/stages.py

def stage_planning(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    agent = deps.agent_factory(allowed_tools=deps.tools.planning_set())
    system_prompt = deps.prompt_builder.build_planner_prompt(
        spec=ctx.spec,
        conventions=deps.conventions.load_for(ctx.target_repo, ctx.spec["pipeline_domain"]),
    )
    user_msg = deps.prompt_builder.format_spec(ctx.spec)

    result = agent.run(system_prompt, user_msg, allowed_tools=deps.tools.planning_set())
    plan = parse_plan(result.text)  # raises if malformed; orchestrator handles retry

    if plan.open_questions:
        ctx.error = json.dumps(plan.open_questions)
        return Stage.PAUSED_FOR_USER

    ctx.plan = plan.dict()
    return Stage.GENERATING


def stage_generating(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    plan = Plan.parse_obj(ctx.plan)
    for file_plan in plan.files:
        skill = pick_skill_for(file_plan)
        agent = deps.agent_factory(allowed_tools=deps.tools.generation_set())
        system_prompt = deps.prompt_builder.build_generator_prompt(
            skill=skill,
            references=relevant_refs(file_plan),
            conventions=deps.conventions.load_for(ctx.target_repo, plan.pipeline_name),
            plan=plan,
            current_file=file_plan,
        )
        user_msg = f"Generate {file_plan.path}. Description: {file_plan.description}"
        result = agent.run(system_prompt, user_msg, allowed_tools=deps.tools.generation_set())

        # The agent calls write_file as a tool, so by the time we get here the file is written
        if not (Path(ctx.workspace_path) / file_plan.path).exists():
            raise GenerationError(f"agent did not write {file_plan.path}")

        ctx.generated_files.append(file_plan.path)

    return Stage.VALIDATING


def stage_validating(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    ctx.validation_attempts += 1
    fmt_diff = deps.tools.invoke("scalafmt", {"paths": ctx.generated_files}, deps.tool_ctx(ctx))
    compile_errors = deps.tools.invoke("scalac", {"paths": ctx.generated_files}, deps.tool_ctx(ctx))
    convention_violations = deps.tools.invoke("convention_check", {"paths": ctx.generated_files}, deps.tool_ctx(ctx))

    if compile_errors or convention_violations:
        if ctx.validation_attempts >= Orchestrator.MAX_VALIDATION_ATTEMPTS:
            ctx.error = format_validation_failure(compile_errors, convention_violations)
            return Stage.FAILED
        return Stage.REPAIRING

    return Stage.TESTING


def stage_repairing(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    # Re-run agent with the validation errors as context, ask it to fix the specific files
    last_errors = ctx.error  # set by validating
    agent = deps.agent_factory(allowed_tools=deps.tools.generation_set())
    system_prompt = deps.prompt_builder.build_repair_prompt(
        errors=last_errors,
        affected_files=ctx.generated_files,
        # narrow context: only the failing files + their referenced siblings
    )
    user_msg = "Fix the validation errors above. Edit only the files needed."
    agent.run(system_prompt, user_msg, allowed_tools=deps.tools.generation_set())

    return Stage.VALIDATING


def stage_testing(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    ctx.test_attempts += 1
    # Split: pure-Scala tests run locally in worker, Spark tests run on devbox
    test_files = [f for f in ctx.generated_files if f.startswith("src/test/")]
    pure_tests, spark_tests = partition_tests(test_files)

    local_failures = []
    if pure_tests:
        local_failures = deps.tools.invoke("run_local_tests", {"specs": pure_tests}, deps.tool_ctx(ctx))

    devbox_failures = []
    if spark_tests and ctx.devbox_host:
        devbox_failures = deps.tools.invoke("run_devbox_tests", {"specs": spark_tests}, deps.tool_ctx(ctx))
    elif spark_tests:
        # No devbox configured; warn but continue
        log.warning("Spark tests skipped: no devbox configured")

    if local_failures or devbox_failures:
        if ctx.test_attempts >= Orchestrator.MAX_TEST_ATTEMPTS:
            ctx.error = format_test_failure(local_failures, devbox_failures)
            return Stage.FAILED
        return Stage.REPAIRING

    return Stage.COMMITTING


def stage_committing(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    branch = make_branch_name(ctx.spec)  # e.g. feature/TSYBSM-77001-repo_funding
    deps.tools.invoke("git_branch", {"name": branch}, deps.tool_ctx(ctx))
    msg = make_commit_message(ctx.plan, ctx.spec)
    deps.tools.invoke("git_commit", {"message": msg, "files": ctx.generated_files}, deps.tool_ctx(ctx))
    deps.tools.invoke("git_push", {"branch": branch}, deps.tool_ctx(ctx))
    return Stage.OPENING_PR


def stage_opening_pr(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    body = make_pr_body(ctx.plan, ctx.spec, ctx.generated_files)
    pr_url = deps.tools.invoke("bitbucket_open_pr", {
        "title": make_pr_title(ctx.spec),
        "body": body,
        "source_branch": current_branch(ctx),
        "target_branch": "develop",
    }, deps.tool_ctx(ctx))
    ctx.pr_url = pr_url
    return Stage.SUCCEEDED
```

### 1.10 Persistence schema

```sql
-- src/saturn_agent/persistence/migrations/001_initial.sql

CREATE TABLE users (
    id          UUID PRIMARY KEY,
    sso_id      TEXT UNIQUE NOT NULL,
    email       TEXT,
    devbox_host TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    spec            JSONB NOT NULL,
    target_repo     TEXT NOT NULL,
    devbox_host     TEXT,
    stage           TEXT NOT NULL,
    plan            JSONB,
    pr_url          TEXT,
    error           TEXT,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    workspace_path  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX idx_jobs_user_status ON jobs(user_id, stage);
CREATE INDEX idx_jobs_created_at ON jobs(created_at DESC);

CREATE TABLE job_events (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL,
    event_type  TEXT NOT NULL,    -- "stage_entered" | "tool_called" | "llm_call" | "error"
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_job_events_job_id ON job_events(job_id, created_at);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID,
    action      TEXT NOT NULL,
    resource    TEXT NOT NULL,
    outcome     TEXT NOT NULL,    -- "success" | "failure" | "denied"
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`job_events` is append-only and gives you the full replay of any job. `audit_log` is for compliance. Both are aggressively indexed for the query patterns the UI needs.

### 1.11 CLI for local dev

```python
# src/saturn_agent/cli.py

import click
from pathlib import Path

@click.group()
def cli():
    pass

@cli.command()
@click.option("--spec", required=True, type=click.Path(exists=True))
@click.option("--repo", required=True, type=click.Path(exists=True))
@click.option("--devbox", default=None, help="Devbox SSH host for Spark tests")
@click.option("--no-pr", is_flag=True, help="Skip PR creation; leave files for review")
def generate(spec: str, repo: str, devbox: str | None, no_pr: bool):
    """Run a job locally. No queue, no API. For dev iteration."""
    spec_data = yaml.safe_load(Path(spec).read_text())
    ctx = JobContext(
        job_id=uuid.uuid4(),
        user_id="local-dev",
        spec=spec_data,
        target_repo=repo,
        workspace_path=tempfile.mkdtemp(prefix="saturn-agent-"),
        devbox_host=devbox,
    )
    deps = build_deps_for_local()
    final_stage = Orchestrator(ctx, deps).run()
    click.echo(f"Final stage: {final_stage}")
    if ctx.pr_url:
        click.echo(f"PR: {ctx.pr_url}")

@cli.command()
@click.argument("repo_path")
def extract_conventions(repo_path: str):
    """Run the convention extractor over a target repo."""
    ...

if __name__ == "__main__":
    cli()
```

This CLI is what you use during weeks of agent-quality iteration. The hosted version comes later.

---

## 2. Repo: `saturn-agent-conventions`

Standalone offline tool. Runs as a cron in production; runs locally during development.

```
saturn-agent-conventions/
├── pyproject.toml
├── src/
│   └── saturn_conventions/
│       ├── cli.py
│       ├── parser.py        # tree-sitter-scala wrapper
│       ├── extractors/
│       │   ├── provider.py  # extracts RocketBuilder chain
│       │   ├── node.py      # extracts node style, params, traits
│       │   ├── domain.py    # extracts case class shapes
│       │   ├── ddl.py       # extracts DDL filename + table conventions
│       │   └── pipeline.py  # orchestrates above per-pipeline-folder
│       ├── schemas/
│       │   └── conventions.py  # pydantic
│       ├── output/
│       │   ├── json_writer.py
│       │   └── markdown_writer.py  # human-readable summary
│       └── publisher.py     # uploads to MinIO
├── tests/
└── deployment/
    └── cronjob.yaml         # k8s CronJob, daily
```

### 2.1 Output schema

```python
# src/saturn_conventions/schemas/conventions.py

class ProviderConvention(BaseModel):
    name: str
    rocket_builder_root_arg: str  # e.g., "fbo_limits_reports"
    global_args: list[tuple[str, str]]
    nodes: list["NodeRegistration"]
    finish_called: bool

class NodeRegistration(BaseModel):
    node_class: str
    node_id: str
    children: list[str]
    node_args: list[tuple[str, str]]

class NodeConvention(BaseModel):
    class_name: str
    file_path: str
    style: str  # "A" | "B" | "C"
    constructor_params: list["ParamConvention"]  # in source order
    return_type: str
    traits: list[str]  # e.g., ["ImpalaUtils"]
    references: list[tuple[str, str]]  # (schema, table) for @Reference annotations

class ParamConvention(BaseModel):
    name: str
    type: str
    naming: str  # "camelCase" | "snake_case"
    annotation: str | None  # e.g., '@Reference("saturn", "cdl_d_party")'

class DomainConvention(BaseModel):
    common_class_fields: list[tuple[str, str]]  # (field_name, type)
    other_case_classes: list["CaseClassConvention"]

class PipelineConvention(BaseModel):
    pipeline_name: str
    folder_path: str
    provider: ProviderConvention
    nodes: list[NodeConvention]
    domain: DomainConvention | None

class RepoConventions(BaseModel):
    repo_name: str
    extracted_at: datetime
    pipelines: list[PipelineConvention]
    common_imports: list[str]  # imports seen in ≥80% of nodes — the "real" cheat-sheet
```

This artifact is uploaded to MinIO at `s3://saturn-agent/conventions/<repo>/<timestamp>.json` and `s3://saturn-agent/conventions/<repo>/latest.json` (overwritten on each run).

### 2.2 Parser approach

Use `tree-sitter-scala`. Build small extractors per construct type. Don't try to fully type-check Scala — just match the patterns the skills care about.

Example extractor:

```python
# src/saturn_conventions/extractors/node.py

def extract_node_convention(file_path: Path, tree) -> NodeConvention:
    class_node = find_top_level_class(tree)
    constructor_params = extract_class_constructor_params(class_node)
    return_type = extract_extends_clause_type(class_node)  # e.g., "Node[Common]"
    traits = extract_with_clauses(class_node)
    refs = [
        (param.annotation_arg(0), param.annotation_arg(1))
        for param in constructor_params
        if param.annotation_name == "Reference"
    ]
    style = classify_style(constructor_params, return_type, refs)
    ...
```

### 2.3 CLI

```bash
saturn-conventions extract --repo /path/to/saturn-pipelines-reports --out conventions.json
saturn-conventions publish --repo saturn-pipelines-reports --json conventions.json
saturn-conventions diff --repo saturn-pipelines-reports --against latest  # shows convention drift
```

The `diff` command is for code reviews when someone changes a pattern.

---

## 3. Repo: `saturn-agent-eval`

Mirrors the worker's structure but for evaluation.

```
saturn-agent-eval/
├── pyproject.toml
├── src/
│   └── saturn_eval/
│       ├── cli.py
│       ├── runner.py        # invokes worker.cli per case
│       ├── grader.py        # mechanical scoring
│       ├── reporter.py      # writes html report + posts to Slack
│       └── dataset.py       # case loader
├── dataset/
│   ├── README.md
│   ├── case_001_simple_workflow_node/
│   │   ├── spec.yaml
│   │   ├── expected_files/
│   │   │   ├── FooNode.scala
│   │   │   └── FooRocketProvider.scala
│   │   ├── grading.yaml
│   │   └── source_pr.txt    # link to original PR for traceability
│   ├── case_002_multi_node_dag/
│   ├── case_003_new_table_with_ddl/
│   └── ...
└── reports/                 # gitignored; eval run outputs
```

### 3.1 Grading config

```yaml
# dataset/case_001/grading.yaml
must_compile: true
must_pass_convention_checks: true
provider:
  must_call_finish: true
  expected_node_ids: [foo_workflow]
  expected_global_args: [lrdCode, reportingViewCode]
  expected_node_arg_pairs:
    - [reportCode, FOO_BO]
    - [reportDescription, "Foo Reports"]
files_must_exist:
  - FooNode.scala
  - FooRocketProvider.scala
test_pass_rate_minimum: 0.8
ddl:
  expected: false
```

Grader runs each gate. Score = passed gates / total gates. Per-case score and aggregate score on every eval run.

### 3.2 Run modes

```bash
saturn-eval run --case case_001                      # one case, full output
saturn-eval run --suite all --workers 4              # parallel
saturn-eval baseline                                 # establish today's score
saturn-eval compare --against last_baseline.json     # regression check
```

Wire `saturn-eval run --suite all` into the worker repo's CI. PR to `saturn-agent-worker` blocked if eval score regresses.

---

## 4. Repo: `saturn-agent-api`

FastAPI server. Thin: auth, request validation, job creation, SSE event stream, file serving from MinIO.

```
saturn-agent-api/
├── pyproject.toml
├── src/
│   └── saturn_api/
│       ├── main.py
│       ├── config.py
│       ├── deps.py             # dependency-injection (db, redis, celery)
│       ├── auth/
│       │   ├── sso.py          # SSO middleware
│       │   └── current_user.py
│       ├── routes/
│       │   ├── jobs.py         # POST /api/jobs, GET /api/jobs/{id}, GET /api/jobs/{id}/events
│       │   ├── conventions.py  # GET /api/conventions/<repo>
│       │   ├── users.py        # GET /api/users/me, PUT /api/users/me/devbox
│       │   └── health.py
│       ├── schemas/
│       │   ├── job.py
│       │   └── spec.py
│       ├── services/
│       │   ├── job_service.py  # creates jobs, dispatches to celery
│       │   ├── event_stream.py # SSE pub/sub via redis
│       │   └── audit.py
│       └── persistence/
│           └── ... (shares schema with worker; could be a shared lib)
└── tests/
```

### 4.1 Routes

```
POST   /api/jobs                        — create a job
GET    /api/jobs/{id}                   — job state + plan + PR URL
GET    /api/jobs/{id}/events            — SSE stream of stage transitions, agent logs
POST   /api/jobs/{id}/cancel            — cancel a running job
POST   /api/jobs/{id}/answer            — answer open_questions when paused
GET    /api/jobs                        — list user's recent jobs (paginated)
GET    /api/conventions/{repo}          — latest conventions JSON for a repo
GET    /api/users/me                    — current user info
PUT    /api/users/me/devbox             — set devbox SSH host
GET    /api/health                      — k8s liveness
GET    /api/ready                       — k8s readiness
```

### 4.2 Job creation flow

```python
@router.post("/api/jobs")
async def create_job(
    body: CreateJobRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    celery_app: Celery = Depends(get_celery),
) -> JobResponse:
    # validate spec against schema
    spec = validate_spec(body.spec)
    # persist job
    job = JobRepository(db).create(
        user_id=user.id,
        spec=spec.dict(),
        target_repo=body.target_repo,
        devbox_host=body.devbox_host or user.devbox_host,
    )
    # enqueue to celery
    celery_app.send_task("saturn_agent.run_job", args=[str(job.id)])
    # audit
    AuditLogger(db).log(user_id=user.id, action="job.create",
                        resource=str(job.id), outcome="success")
    return JobResponse.from_orm(job)
```

### 4.3 SSE event stream

Worker publishes events to Redis on a channel `job_events:<job_id>`. API server subscribes, forwards to the connected client. Standard pattern:

```python
@router.get("/api/jobs/{job_id}/events")
async def stream_events(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    job = JobRepository(db).get_for_user(job_id, user.id)
    if not job:
        raise HTTPException(404)

    async def gen():
        # replay any events that already happened
        for event in JobEventRepository(db).list_for_job(job_id):
            yield format_sse(event)
        # then subscribe to live events
        async for msg in redis.subscribe(f"job_events:{job_id}"):
            yield format_sse(msg)
            if msg["event_type"] == "stage_entered" and msg["stage"] in TERMINAL_STAGES:
                break

    return StreamingResponse(gen(), media_type="text/event-stream")
```

---

## 5. Repo: `saturn-agent-frontend`

```
saturn-agent-frontend/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   ├── client.ts       # fetch wrapper with auth
│   │   └── jobs.ts         # typed client for jobs endpoints
│   ├── pages/
│   │   ├── NewJob.tsx      # the spec form
│   │   ├── JobDetail.tsx   # progress, logs, PR link
│   │   ├── JobsList.tsx
│   │   └── Settings.tsx    # devbox host
│   ├── components/
│   │   ├── SpecForm/
│   │   │   ├── index.tsx
│   │   │   ├── InputTablesField.tsx
│   │   │   ├── OutputTablesField.tsx
│   │   │   └── TransformationsField.tsx
│   │   ├── JobProgress/
│   │   │   ├── index.tsx
│   │   │   └── StageBar.tsx
│   │   ├── EventStream.tsx
│   │   └── PRPreview.tsx
│   ├── hooks/
│   │   ├── useJob.ts
│   │   ├── useJobEvents.ts # SSE subscription
│   │   └── useAuth.ts
│   ├── store/              # tanstack-query setup
│   └── types/
│       └── api.ts          # mirrored from backend pydantic schemas
└── public/
```

### 5.1 The spec form

The most important UI element. Shapes the user's mental model.

Sections:
- **Pipeline metadata**: name, target repo (dropdown), JIRA ticket
- **Input tables**: dynamic list. Each entry: table name, schema (CSV-pasteable), source (Hive / CDL via @Reference)
- **Output tables**: dynamic list. Each entry: table name, schema, exists?, daily volume tier
- **Transformations**: large textarea, with prompt-help showing what "mechanism-level" means
- **Args**: `entity_code`, `reportCode`, etc.
- **Devbox**: prefilled from user settings, overrideable

Validation client-side mirrors backend pydantic. Submit creates job, redirects to `/jobs/<id>`.

### 5.2 The job detail view

- **Stage progress bar** at top: Init → Planning → Generating → Validating → Testing → PR
- **Event stream** below: structured log entries from SSE, expandable per-stage
- **Plan panel** (visible after planning succeeds): list of files about to be generated, assumptions, open questions
- **Generated files panel**: tabs per file, with syntax-highlighted diff against the empty starting state
- **PR link** prominent at top once available
- **Re-run button** that pre-fills the form with this job's spec

If `stage = PAUSED_FOR_USER`, show the open_questions inline with answer fields. POST to `/api/jobs/{id}/answer` to resume.

---

## 6. Cross-cutting concerns

### 6.1 Prompt versioning

Every prompt template is a file in `saturn-agent-worker/prompts/`. Files are versioned: `planner_v3.md`, not `planner.md`. The active version is selected via config. Add new versions, A/B test against eval, switch when proven.

```
saturn-agent-worker/src/saturn_agent/agent/prompts/
├── planner/
│   ├── v1.md
│   ├── v2.md
│   └── v3.md   # current
├── generator/
│   └── v1.md
└── repair/
    └── v1.md
```

`prompt_builder.py` resolves the active version from settings.

### 6.2 Token budgeting

Hard limits per job, per stage, per LLM call:

```python
# src/saturn_agent/agent/token_budget.py

class TokenBudget:
    def __init__(self, max_total: int):
        self.max_total = max_total
        self.used = 0

    def check(self) -> None:
        if self.used >= self.max_total:
            raise TokenBudgetExceeded(used=self.used, max=self.max_total)

    def add(self, n: int) -> None:
        self.used += n
```

Defaults: 500k tokens per job, hard. Surface as a metric. If 90th percentile is creeping toward the limit, that's a signal to refactor prompts.

### 6.3 Error taxonomy

Three error families, handled differently:

- **`UserError`** — bad spec, ambiguous transformations, missing JIRA. Surface to UI; do not retry.
- **`SystemError`** — Gemini timeout, Bitbucket 5xx, devbox SSH down. Retry with exponential backoff (Celery handles), surface after final retry.
- **`AgentError`** — LLM returned malformed JSON, agent loop exhausted, validator failed after max retries. Persist full transcript to MinIO; surface a sanitized message to user with a "view trace" admin link.

Define these as concrete exception classes. Every handler in the orchestrator distinguishes them.

### 6.4 Concurrency limits

- Per user: max 3 concurrent running jobs (queued OK).
- Per worker pod: 1 job at a time (CPU/memory bound, especially during compile).
- Per devbox host: 1 SSH command at a time (Redis lock, key = `devbox:<host>`, TTL = 10 minutes).
- Per LLM rate: handled by Gemini API itself; if rate-limited, exponential backoff in the agent client.

### 6.5 Observability — explicit events

Events to emit (structured, JSON, with `job_id` and `user_id`):

| Event | When | Fields |
|---|---|---|
| `job.created` | API creates job | spec_summary, target_repo |
| `job.queued` | Celery accepts task | |
| `stage.entered` | Each orchestrator stage | stage, attempt |
| `stage.exited` | Each stage | stage, duration_ms, outcome |
| `llm.called` | Each Gemini call | tokens_in, tokens_out, latency_ms, model |
| `tool.invoked` | Each tool call | tool_name, args_summary, latency_ms, outcome |
| `validation.failed` | After validate stage if failures | violations_count, attempt |
| `test.failed` | After test stage if failures | failed_specs, attempt |
| `pr.opened` | After PR creation | pr_url, branch |
| `job.succeeded` | Terminal happy | total_duration_ms, total_tokens, pr_url |
| `job.failed` | Terminal sad | error_class, error_summary |

Every event lands in three places: structured log (for grep), Postgres `job_events` (for replay/UI), Prometheus counter/histogram (for dashboards).

### 6.6 Security

- **Workspace sandbox**: write_file rejects paths outside `workspace_path`. Tool implementations Path.resolve() and check is_relative_to. No exceptions.
- **SSH key handling**: user's key never logged, never persisted in plaintext. Encrypted at rest (KMS or libsodium); decrypted only in worker process memory.
- **Bitbucket token**: in k8s Secret, mounted read-only, never logged. Rotate quarterly.
- **Spec validation**: prevents shell injection in things like JIRA ticket (used in branch names). Allow-list characters: `[A-Z0-9-_]`.
- **Eval dataset is non-PII**: pull from public-equivalent test fixtures, not real production specs.

### 6.7 Config matrix

| Env | Where | What |
|---|---|---|
| local dev | `.env` file, gitignored | Points at local Postgres, fake LLM, fake Bitbucket |
| eval CI | injected by CI | Real Gemini, mock Bitbucket, ephemeral Postgres |
| staging | k8s ConfigMap + Secret | Real Gemini, real Bitbucket, staging branch convention |
| prod | k8s ConfigMap + Secret | Real everything; SSO required |

Never have config defaults that work in prod. Make accidental misconfiguration a hard failure at startup.

---

## 7. Build order, dependency-first

This is what to hand to Claude Code, in order:

1. **`saturn-agent-eval`**: dataset loader + grader + 5 stub cases (no real expected_files yet — just `must_compile: true`). Lets you measure anything you build.
2. **`saturn-agent-conventions`**: extractor for one pipeline (`fbo_limits`), tree-sitter parser, JSON output. Test against the existing `saturn-pipelines-reports` repo.
3. **`saturn-agent-worker` foundation**: config, persistence models (Alembic migrations), workspace manager, Gemini client (mockable), tool registry skeleton.
4. **`saturn-agent-worker` tools — passive set**: `read_file`, `list_dir`, `grep`, `find_sibling`, `read_skill`, `read_reference`, `read_conventions`. Unit tests for each.
5. **`saturn-agent-worker` tools — emit + validate**: `write_file`, `scalafmt`, `scalac`, `convention_check` (start with 5 rules, grow as eval surfaces issues).
6. **`saturn-agent-worker` agent core**: `AgentToolLoop`, `prompt_builder`, `token_budget`. Integrate with Gemini.
7. **`saturn-agent-worker` orchestrator + CLI**: state machine, stage handlers for plan + generate + validate + repair (no test, no PR yet). End-to-end from CLI.
8. **Iterate against eval**: run dataset, find gaps, add convention rules + reference content + prompt iterations until eval baseline is acceptable.
9. **Tools — test execution**: `run_local_tests`, `run_devbox_tests`. Add testing stage to orchestrator.
10. **Tools — VCS + PR**: `git_*`, `bitbucket_*`. Add committing + PR stages.
11. **`saturn-agent-api`**: FastAPI server, Postgres + Celery, SSE event stream.
12. **`saturn-agent-frontend`**: React UI, SpecForm, JobDetail.
13. **Observability everywhere**: prometheus metrics, OTLP traces, structured logging shipper.
14. **Hardening**: SSO, audit log, secrets, rate limits, k8s manifests, runbooks.

Steps 1–8 are the high-risk, high-reward path. Get the agent producing decent code before investing in hosting infra. Steps 11–14 are well-trodden engineering and shouldn't surprise.

---

## 8. What this design deliberately does not have

So Claude Code doesn't accidentally build it:

- **No multi-agent communication.** One agent, multiple skills. Don't spawn a planner subprocess that talks to a coder subprocess.
- **No LangGraph.** The state machine is 200 lines of Python.
- **No streaming the LLM tokens to the user.** Logs and stage transitions are enough. Token-by-token streaming complicates the SSE story for negligible UX gain.
- **No agent self-evaluation.** Eval is mechanical, separate, run by humans. Letting the agent grade itself is a bias trap.
- **No "model router" abstraction for v1.** Gemini-only. Add abstraction when you have a second model to route to.
- **No memory / RAG / vector DB.** Conventions cache + tree-sitter extraction is the structured equivalent. Reach for vector search only when you have a use case it actually fits.
- **No fine-tuning in v1.** First exhaust prompt engineering, examples, references. Fine-tuning is a multi-week investment with its own ops surface.
- **No automatic PR merging.** Agent opens; humans review and merge. Always.
- **No pipeline editing in v1.** Agent only creates new pipelines / adds new nodes to existing ones. "Modify this existing node to also do X" is harder than it sounds and is phase 2.

---

## 9. First-week tasks for Claude Code

Concrete starting points:

1. Initialize all five repos with `pyproject.toml` / `package.json`, ruff/mypy/eslint configs, basic CI (lint + test).
2. In `saturn-agent-eval`, build the case loader and grader with 1 stub case. Verify `pytest` runs.
3. In `saturn-agent-conventions`, install `tree-sitter` + `tree-sitter-scala`, parse one real `.scala` file from `saturn-pipelines-reports`, print the tree. Confirm the grammar covers what you need.
4. In `saturn-agent-worker`, build `config.py` + `persistence/models.py` + Alembic init. Run `alembic upgrade head` against a local Postgres.
5. In `saturn-agent-worker`, implement `read_file` and `list_dir` tools with full unit tests. These are the simplest tools and exercise the full `Tool` protocol.

If any of these takes more than a day, the design has a problem and we should adjust before building further.
