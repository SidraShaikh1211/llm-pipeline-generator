from __future__ import annotations

from pathlib import Path

from agent_worker.agent.event_recorder import JobEventRecorder
from agent_worker.conventions.loader import load_conventions, pipeline_name_from_spec
from agent_worker.observability.logging import get_logger
from agent_worker.orchestrator.dag import DAGError, validate_and_sort
from agent_worker.orchestrator.deps import (
    GENERATION_TOOLS,
    PLANNING_TOOLS,
    REPAIR_TOOLS,
    OrchestratorDeps,
)
from agent_worker.orchestrator.plan import Plan, PlanParseError, parse_plan
from agent_worker.orchestrator.state_machine import JobContext, Orchestrator, Stage
from agent_worker.workspace.repo_clone import RepoCloneError

log = get_logger(__name__)


# ---------- INIT ----------


def stage_init(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    if not ctx.workspace_path.is_dir():
        ctx.error = f"workspace does not exist: {ctx.workspace_path}"
        return Stage.FAILED

    source = ctx.spec.get("target_repo_source")
    if source:
        repo_dir = ctx.workspace_path / "repos" / ctx.target_repo
        if repo_dir.exists() and any(repo_dir.iterdir()):
            deps.repository.append_event(
                job_id=ctx.job_id,
                stage=Stage.INIT.value,
                event_type="repo.skipped",
                payload={"path": str(repo_dir), "reason": "already populated"},
            )
        else:
            branch = ctx.spec.get("target_repo_branch")
            try:
                deps.repo_clone_service.clone(
                    str(source), repo_dir, branch=str(branch) if branch else None
                )
            except RepoCloneError as e:
                ctx.error = f"repo clone failed: {e}"
                deps.repository.append_event(
                    job_id=ctx.job_id,
                    stage=Stage.INIT.value,
                    event_type="repo.clone_failed",
                    payload={"source": str(source), "error": str(e)},
                )
                return Stage.FAILED
            deps.repository.append_event(
                job_id=ctx.job_id,
                stage=Stage.INIT.value,
                event_type="repo.cloned",
                payload={
                    "source": str(source),
                    "path": str(repo_dir),
                    "branch": str(branch) if branch else None,
                },
            )

    return Stage.PLANNING


# ---------- PLANNING ----------


def stage_planning(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    conventions = load_conventions(
        deps.conventions_path, pipeline_name_from_spec(ctx.spec)
    )
    system_prompt = deps.prompt_builder.build_planner_prompt(
        spec=ctx.spec, conventions=conventions
    )
    user_msg = "Produce the Plan JSON for this spec."

    loop = deps.agent_loop(
        recorder=JobEventRecorder(deps.repository, ctx.job_id, Stage.PLANNING.value)
    )
    try:
        result = loop.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            allowed_tools=list(PLANNING_TOOLS),
            ctx=deps.tool_ctx(ctx),
        )
    except Exception as e:  # AgentLoopExhausted, TokenBudgetExceeded, etc.
        ctx.error = f"planning failed: {e}"
        return Stage.FAILED

    try:
        plan = parse_plan(result.text)
    except PlanParseError as e:
        ctx.error = f"plan parse error: {e}"
        deps.repository.append_event(
            job_id=ctx.job_id,
            stage=Stage.PLANNING.value,
            event_type="plan.parse_error",
            payload={"error": str(e)},
        )
        return Stage.FAILED

    ctx.plan = plan.model_dump(mode="json")
    ctx.tokens_used = deps.budget.used

    deps.repository.append_event(
        job_id=ctx.job_id,
        stage=Stage.PLANNING.value,
        event_type="plan.parsed",
        payload={
            "files": [f.path for f in plan.files],
            "estimated_tokens": plan.estimated_tokens,
            "skills_required": plan.skills_required,
        },
    )

    if plan.open_questions:
        ctx.open_questions = list(plan.open_questions)
        ctx.error = "spec is incomplete; planner raised open questions"
        return Stage.PAUSED_FOR_USER

    if not plan.files:
        ctx.error = "planner returned no files to generate"
        return Stage.FAILED

    return Stage.OPTIMIZING_PLAN


# ---------- OPTIMIZING_PLAN ----------


def stage_optimizing_plan(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    """Validate the plan's depends_on graph and topologically sort plan.files.

    Pure deterministic pass — no LLM, no I/O. Catches cycles, dangling deps,
    and duplicate paths up-front so we don't waste generation calls before
    scalac trips on them. The reordered ``ctx.plan`` is what stage_generating
    iterates, and the order matters: it controls which upstream file bodies
    are available in ``ctx.generated_files_content`` when downstream files are
    generated.

    Cost note: injecting upstream bodies grows the per-file generation prompt
    linearly with DAG depth. For deep pipelines a future slice may want a
    summary-only mode.
    """
    if ctx.plan is None:
        ctx.error = "optimizing_plan entered with no plan"
        return Stage.FAILED

    plan = Plan.model_validate(ctx.plan)
    try:
        sorted_plan = validate_and_sort(plan)
    except DAGError as e:
        ctx.error = f"DAG validation failed: {e}"
        deps.repository.append_event(
            job_id=ctx.job_id,
            stage=Stage.OPTIMIZING_PLAN.value,
            event_type="dag.invalid",
            payload={"error": str(e), "kind": type(e).__name__},
        )
        return Stage.FAILED

    ctx.plan = sorted_plan.model_dump(mode="json")
    deps.repository.append_event(
        job_id=ctx.job_id,
        stage=Stage.OPTIMIZING_PLAN.value,
        event_type="dag.validated",
        payload={"order": [f.path for f in sorted_plan.files]},
    )
    return Stage.GENERATING


# ---------- GENERATING ----------


def stage_generating(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    if ctx.plan is None:
        ctx.error = "generating stage entered with no plan"
        return Stage.FAILED
    plan = Plan.model_validate(ctx.plan)

    # Load conventions once per stage; the slice is the same for every file
    # generated within this job.
    conventions = load_conventions(
        deps.conventions_path, pipeline_name_from_spec(ctx.spec)
    )

    for file_plan in plan.files:
        skill = _pick_skill_for(file_plan.kind)
        try:
            skill_text = (deps.skills_root / skill / "SKILL.md").read_text(
                encoding="utf-8"
            )
        except OSError as e:
            ctx.error = f"could not load skill {skill!r}: {e}"
            return Stage.FAILED

        system_prompt = deps.prompt_builder.build_generator_prompt(
            skill_text=skill_text,
            references=None,
            conventions=conventions,
            plan=ctx.plan,
            current_file=file_plan.model_dump(mode="json"),
        )
        user_msg = f"Generate {file_plan.path}. {file_plan.description}"
        # Append already-generated upstream file bodies so the LLM sees the
        # exact contracts it must match instead of re-deriving them from the
        # plan description. Kept in the user message to preserve prompt
        # caching on the system block.
        upstream_blocks: list[str] = []
        for dep in file_plan.depends_on:
            body = ctx.generated_files_content.get(dep)
            if body is not None:
                upstream_blocks.append(f"### {dep}\n```\n{body}\n```")
        if upstream_blocks:
            user_msg += "\n\n## Upstream files (already generated)\n" + "\n\n".join(
                upstream_blocks
            )

        loop = deps.agent_loop(
            recorder=JobEventRecorder(
                deps.repository, ctx.job_id, Stage.GENERATING.value
            )
        )
        try:
            loop.run(
                system_prompt=system_prompt,
                user_message=user_msg,
                allowed_tools=list(GENERATION_TOOLS),
                ctx=deps.tool_ctx(ctx),
            )
        except Exception as e:
            ctx.error = f"generation failed for {file_plan.path}: {e}"
            return Stage.FAILED

        # Plan paths are repo-relative (e.g., "src/main/scala/..."), but when
        # a target repo has been cloned under <workspace>/repos/<target_repo>/
        # the agent typically writes there to inherit the repo's classpath and
        # .scalafmt.conf. Accept either layout, and always store paths
        # workspace-relative so resolve_within_workspace finds them later.
        clone_dir = ctx.workspace_path / "repos" / ctx.target_repo
        target: Path | None = None
        for candidate in (clone_dir / file_plan.path, ctx.workspace_path / file_plan.path):
            if candidate.is_file():
                target = candidate
                break
        if target is None:
            ctx.error = (
                f"agent did not write {file_plan.path}; check generation prompt + tools"
            )
            deps.repository.append_event(
                job_id=ctx.job_id,
                stage=Stage.GENERATING.value,
                event_type="generation.missing_file",
                payload={"path": file_plan.path},
            )
            return Stage.FAILED
        workspace_rel = str(
            target.resolve().relative_to(ctx.workspace_path.resolve())
        )
        ctx.generated_files.append(workspace_rel)
        try:
            ctx.generated_files_content[file_plan.path] = target.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            # Best-effort: if we can't read it back, downstream files just
            # won't see this upstream body. The file itself is on disk and
            # validation will still pick it up.
            pass
        deps.repository.append_event(
            job_id=ctx.job_id,
            stage=Stage.GENERATING.value,
            event_type="file.generated",
            payload={
                "path": file_plan.path,
                "kind": file_plan.kind,
                "bytes": target.stat().st_size,
            },
        )

    ctx.tokens_used = deps.budget.used
    return Stage.VALIDATING


def _pick_skill_for(kind: str) -> str:
    if kind == "ddl":
        return "ddl-builder"
    if kind in {"test_spec", "test_fixture"}:
        return "pipeline-tester"
    return "pipeline-builder"


# ---------- VALIDATING ----------


def stage_validating(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    ctx.validation_attempts += 1
    if not ctx.generated_files:
        ctx.error = "validation entered with no generated files"
        return Stage.FAILED

    tool_ctx = deps.tool_ctx(ctx)
    scala_files = [f for f in ctx.generated_files if f.endswith(".scala")]

    fmt_msg = ""
    if scala_files:
        fmt_msg = deps.registry.invoke(
            "scalafmt", {"paths": scala_files}, tool_ctx
        )

    compile_msg = ""
    if scala_files:
        compile_msg = deps.registry.invoke(
            "scalac", {"paths": scala_files}, tool_ctx
        )

    convention_msg = deps.registry.invoke(
        "convention_check", {"paths": ctx.generated_files}, tool_ctx
    )

    compile_failed = compile_msg.startswith("scalac failed")
    convention_failed = not convention_msg.startswith("convention_check ok")

    if compile_failed or convention_failed:
        ctx.error = "\n\n".join(
            part
            for part in (
                f"[scalafmt] {fmt_msg}" if fmt_msg else "",
                f"[scalac] {compile_msg}" if compile_failed else "",
                f"[convention_check] {convention_msg}" if convention_failed else "",
            )
            if part
        )
        deps.repository.append_event(
            job_id=ctx.job_id,
            stage=Stage.VALIDATING.value,
            event_type="validation.failed",
            payload={
                "attempt": ctx.validation_attempts,
                "compile_failed": compile_failed,
                "convention_failed": convention_failed,
            },
        )
        if ctx.validation_attempts >= Orchestrator.MAX_VALIDATION_ATTEMPTS:
            return Stage.FAILED
        return Stage.REPAIRING

    deps.repository.append_event(
        job_id=ctx.job_id,
        stage=Stage.VALIDATING.value,
        event_type="validation.passed",
        payload={"attempt": ctx.validation_attempts},
    )
    ctx.error = None
    return Stage.TESTING


# ---------- REPAIRING ----------


def stage_repairing(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    last_errors = ctx.error or "(no error captured)"
    system_prompt = deps.prompt_builder.build_repair_prompt(
        errors=last_errors, affected_files=ctx.generated_files
    )
    user_msg = "Fix the validation errors above. Edit only the files needed."

    loop = deps.agent_loop(
        recorder=JobEventRecorder(deps.repository, ctx.job_id, Stage.REPAIRING.value)
    )
    try:
        loop.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            allowed_tools=list(REPAIR_TOOLS),
            ctx=deps.tool_ctx(ctx),
        )
    except Exception as e:
        ctx.error = f"repair failed: {e}"
        return Stage.FAILED

    return Stage.VALIDATING


# ---------- TESTING ----------


_SPARK_MARKERS = ("org.apache.spark", "SparkSession", "import spark.")


def partition_tests(
    test_files: list[str], workspace_path
) -> tuple[list[str], list[str]]:
    """Split test files into (pure_scala, spark) by import-grep on file content.

    Files that fail to read are dropped — they show up earlier as missing-file
    failures during generation/validation.
    """
    pure: list[str] = []
    spark: list[str] = []
    for rel in test_files:
        path = workspace_path / rel
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(marker in content for marker in _SPARK_MARKERS):
            spark.append(rel)
        else:
            pure.append(rel)
    return pure, spark


def stage_testing(ctx: JobContext, deps: OrchestratorDeps) -> Stage:
    ctx.test_attempts += 1

    # Match repo-relative ("src/test/...") or workspace-relative
    # ("repos/<repo>/src/test/...") forms — see stage_generating's path
    # resolution above.
    test_files = [
        f for f in ctx.generated_files
        if f.startswith("src/test/") or "/src/test/" in f
    ]
    if not test_files:
        deps.repository.append_event(
            job_id=ctx.job_id,
            stage=Stage.TESTING.value,
            event_type="testing.skipped",
            payload={"reason": "no test files generated"},
        )
        ctx.error = None
        return Stage.SUCCEEDED

    pure_specs, spark_specs = partition_tests(test_files, ctx.workspace_path)
    tool_ctx = deps.tool_ctx(ctx)

    local_msg = ""
    if pure_specs:
        local_msg = deps.registry.invoke(
            "run_local_tests", {"specs": pure_specs}, tool_ctx
        )
    devbox_msg = ""
    if spark_specs:
        devbox_msg = deps.registry.invoke(
            "run_devbox_tests", {"specs": spark_specs}, tool_ctx
        )

    local_failed = local_msg.startswith("sbt test failed")
    devbox_failed = devbox_msg.startswith("devbox tests failed")

    if local_failed or devbox_failed:
        ctx.error = "\n\n".join(
            part
            for part in (
                f"[run_local_tests] {local_msg}" if local_failed else "",
                f"[run_devbox_tests] {devbox_msg}" if devbox_failed else "",
            )
            if part
        )
        deps.repository.append_event(
            job_id=ctx.job_id,
            stage=Stage.TESTING.value,
            event_type="testing.failed",
            payload={
                "attempt": ctx.test_attempts,
                "local_failed": local_failed,
                "devbox_failed": devbox_failed,
                "pure_count": len(pure_specs),
                "spark_count": len(spark_specs),
            },
        )
        if ctx.test_attempts >= Orchestrator.MAX_TEST_ATTEMPTS:
            return Stage.FAILED
        return Stage.REPAIRING

    deps.repository.append_event(
        job_id=ctx.job_id,
        stage=Stage.TESTING.value,
        event_type="testing.passed",
        payload={
            "attempt": ctx.test_attempts,
            "pure_count": len(pure_specs),
            "spark_count": len(spark_specs),
        },
    )
    ctx.error = None
    return Stage.SUCCEEDED


def default_handlers() -> dict[Stage, "object"]:
    """Stage → handler map for the standard pipeline.

    Closes the loop at TESTING. COMMITTING/OPENING_PR are intentionally left
    unimplemented — users open PRs manually after a SUCCEEDED run.
    """
    return {
        Stage.INIT: stage_init,
        Stage.PLANNING: stage_planning,
        Stage.OPTIMIZING_PLAN: stage_optimizing_plan,
        Stage.GENERATING: stage_generating,
        Stage.VALIDATING: stage_validating,
        Stage.REPAIRING: stage_repairing,
        Stage.TESTING: stage_testing,
    }
