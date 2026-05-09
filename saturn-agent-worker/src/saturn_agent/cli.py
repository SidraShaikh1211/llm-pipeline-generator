from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
import yaml

from saturn_agent.agent.client import (
    FakeLLMClient,
    GeminiClient,
    LLMClient,
    load_fake_client_from_script,
)
from saturn_agent.agent.prompt_builder import PromptBuilder
from saturn_agent.agent.token_budget import TokenBudget
from saturn_agent.config import get_settings
from saturn_agent.observability.logging import configure_logging
from saturn_agent.orchestrator import (
    JobContext,
    Orchestrator,
    OrchestratorDeps,
    Stage,
    default_handlers,
)
from saturn_agent.persistence import InMemoryJobRepository
from saturn_agent.tools import builtin_registry


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SKILLS_ROOT = _REPO_ROOT / "skills"


def _load_dotenv_into_environ() -> None:
    """Bridge .env values into os.environ so third-party SDKs (Anthropic,
    Google) see their native env vars. pydantic-settings only puts .env
    contents on Settings fields, not in os.environ."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (Path.cwd() / ".env", _REPO_ROOT.parent / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


_load_dotenv_into_environ()

_EXIT_BY_STAGE: dict[Stage, int] = {
    Stage.SUCCEEDED: 0,
    Stage.FAILED: 1,
    Stage.PAUSED_FOR_USER: 2,
}


@click.group()
def cli() -> None:
    pass


@cli.command()
def version() -> None:
    from saturn_agent import __version__

    click.echo(__version__)


@cli.command()
@click.option(
    "--spec",
    "spec_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the spec YAML file.",
)
@click.option(
    "--workspace",
    "workspace_path",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Workspace directory; created if missing.",
)
@click.option(
    "--target-repo",
    default=None,
    help="Target repo name (override spec.target_repo).",
)
@click.option(
    "--target-repo-source",
    default=None,
    help=(
        "Optional git URL or local path to clone the target repo from before planning. "
        "Lands at <workspace>/repos/<target_repo>/. Local paths use git's hardlink mode."
    ),
)
@click.option(
    "--target-repo-branch",
    default=None,
    help="Branch to check out (defaults to the repo's default branch).",
)
@click.option(
    "--skills-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Path to the skills folder (defaults to the worker's bundled skills/).",
)
@click.option(
    "--conventions",
    "conventions_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a conventions JSON file produced by saturn-conventions.",
)
@click.option(
    "--mock-script",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a JSON file with scripted LLM responses; bypasses real Gemini.",
)
@click.option(
    "--max-tokens",
    type=int,
    default=None,
    help="Per-job token budget; defaults to settings.max_tokens_per_job.",
)
@click.option(
    "--user-id",
    default="local-dev",
    help="User identity recorded on the job.",
)
@click.option(
    "--json-output",
    is_flag=True,
    help="Print a JSON summary on stdout instead of human-readable text.",
)
@click.option("-v", "--verbose", count=True, help="Increase log verbosity.")
def generate(
    spec_path: Path,
    workspace_path: Path,
    target_repo: str | None,
    target_repo_source: str | None,
    target_repo_branch: str | None,
    skills_root: Path | None,
    conventions_path: Path | None,
    mock_script: Path | None,
    max_tokens: int | None,
    user_id: str,
    json_output: bool,
    verbose: int,
) -> None:
    """Run the agent against a spec and emit files into the workspace.

    Exit codes: 0 SUCCEEDED, 1 FAILED, 2 PAUSED_FOR_USER, 3 unexpected error.
    """
    log_level_int = logging.WARNING - 10 * min(verbose, 2)
    configure_logging(level=logging.getLevelName(log_level_int), json_output=True)

    spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec_data, dict):
        raise click.ClickException(f"spec must be a YAML mapping, got {type(spec_data).__name__}")

    # CLI flags override spec-level entries
    if target_repo_source:
        spec_data["target_repo_source"] = target_repo_source
    if target_repo_branch:
        spec_data["target_repo_branch"] = target_repo_branch

    workspace_path.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    skills_root = skills_root or _DEFAULT_SKILLS_ROOT
    references_root = skills_root / "references"

    llm_client: LLMClient
    if mock_script is not None:
        llm_client = load_fake_client_from_script(mock_script)
    else:
        llm_client = GeminiClient(settings)  # raises on call until real adapter lands

    repository = InMemoryJobRepository()
    deps = OrchestratorDeps(
        llm_client=llm_client,
        registry=builtin_registry(),
        budget=TokenBudget(max_total=max_tokens or settings.max_tokens_per_job),
        prompt_builder=PromptBuilder(),
        skills_root=skills_root,
        references_root=references_root,
        conventions_path=conventions_path,
        repository=repository,
    )

    ctx = JobContext(
        spec=spec_data,
        target_repo=target_repo or str(spec_data.get("target_repo") or ""),
        workspace_path=workspace_path,
        user_id=user_id,
    )

    final = Orchestrator(ctx, deps, handlers=default_handlers()).run()

    journal_path = workspace_path / "job_journal.json"
    job_record = repository.get_job(ctx.job_id)
    journal = {
        "job": job_record.to_json_safe() if job_record else None,
        "events": [e.to_json_safe() for e in repository.list_events(ctx.job_id)],
    }
    journal_path.write_text(json.dumps(journal, indent=2))

    if json_output:
        click.echo(
            json.dumps(
                {
                    "job_id": str(ctx.job_id),
                    "stage": final.value,
                    "tokens_used": ctx.tokens_used,
                    "generated_files": ctx.generated_files,
                    "error": ctx.error,
                    "open_questions": ctx.open_questions,
                    "workspace_path": str(workspace_path),
                },
                indent=2,
            )
        )
    else:
        click.echo(f"final stage: {final.value}")
        click.echo(f"tokens used: {ctx.tokens_used}")
        if ctx.generated_files:
            click.echo("generated:")
            for f in ctx.generated_files:
                click.echo(f"  {f}")
        if ctx.open_questions:
            click.echo("open_questions:")
            for q in ctx.open_questions:
                click.echo(f"  - {q}")
        if ctx.error:
            click.echo(f"error: {ctx.error}", err=True)

    sys.exit(_EXIT_BY_STAGE.get(final, 3))


if __name__ == "__main__":
    cli()
