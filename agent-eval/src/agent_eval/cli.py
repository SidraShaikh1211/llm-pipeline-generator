from __future__ import annotations

from pathlib import Path

import click

from agent_eval.dataset import load_case, load_dataset
from agent_eval.grader import Grader, SuiteScore
from agent_eval.reporter import render_text_summary, write_json_report
from agent_eval.runner import RunnerError, RunnerOptions, WorkerRunner


DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "dataset"


@click.group()
def cli() -> None:
    pass


@cli.command("list")
@click.option("--dataset", type=click.Path(path_type=Path), default=DEFAULT_DATASET)
def list_cases(dataset: Path) -> None:
    cases = load_dataset(dataset)
    for c in cases:
        click.echo(f"{c.case_id}\t{c.spec.get('pipeline_name', '?')}")


@cli.command("show")
@click.argument("case_id")
@click.option("--dataset", type=click.Path(path_type=Path), default=DEFAULT_DATASET)
def show(case_id: str, dataset: Path) -> None:
    case = load_case(dataset / case_id)
    click.echo(f"case: {case.case_id}")
    click.echo(f"spec keys: {sorted(case.spec.keys())}")
    click.echo(f"gates configured:")
    g = case.grading
    if g.must_compile:
        click.echo("  - must_compile")
    if g.must_pass_convention_checks:
        click.echo("  - must_pass_convention_checks")
    if g.files_must_exist:
        click.echo(f"  - files_must_exist ({len(g.files_must_exist)})")
    if g.test_pass_rate_minimum is not None:
        click.echo(f"  - test_pass_rate_minimum >= {g.test_pass_rate_minimum}")
    if g.provider is not None:
        click.echo("  - provider")
    if g.ddl is not None and g.ddl.expected:
        click.echo("  - ddl")


@cli.command("run")
@click.argument("case_id", required=False)
@click.option("--dataset", type=click.Path(path_type=Path), default=DEFAULT_DATASET)
@click.option(
    "--output-root",
    type=click.Path(path_type=Path),
    default=lambda: Path.cwd() / "reports",
    show_default="<cwd>/reports",
    help="Where to land per-case workspaces and the suite report.",
)
@click.option(
    "--worker-cli",
    default=None,
    help=(
        "Override the agent-worker CLI. Accepts a single executable or a "
        "shell-style string like 'python -m agent_worker.cli'."
    ),
)
@click.option(
    "--skills-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--conventions",
    "conventions_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--report-json",
    type=click.Path(path_type=Path),
    default=None,
    help="If set, write the SuiteScore JSON report to this path.",
)
def run(
    case_id: str | None,
    dataset: Path,
    output_root: Path,
    worker_cli: str | None,
    skills_root: Path | None,
    conventions_path: Path | None,
    report_json: Path | None,
) -> None:
    """Run the worker against case(s), grade outputs, and write a report."""
    cases = (
        [load_case(dataset / case_id)] if case_id else load_dataset(dataset)
    )
    if not cases:
        raise click.ClickException(f"no cases found under {dataset}")

    output_root.mkdir(parents=True, exist_ok=True)
    options = RunnerOptions(
        worker_cli=worker_cli.split() if worker_cli else None,
        skills_root=skills_root,
        conventions_path=conventions_path,
    )
    runner = WorkerRunner(options=options)
    grader = Grader()
    suite = SuiteScore()

    failures = 0
    for case in cases:
        try:
            output = runner.run(case, output_root)
        except RunnerError as e:
            click.echo(f"[{case.case_id}] runner error: {e}", err=True)
            failures += 1
            continue
        score = grader.grade(case, output)
        suite.cases.append(score)

    click.echo(render_text_summary(suite))
    if report_json is not None:
        write_json_report(suite, report_json)
        click.echo(f"wrote {report_json}")
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    cli()
