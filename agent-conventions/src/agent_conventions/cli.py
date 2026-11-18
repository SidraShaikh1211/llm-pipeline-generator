from __future__ import annotations

from pathlib import Path

import click

from agent_conventions.extractors.pipeline import extract_pipeline, extract_repo
from agent_conventions.output.json_writer import write_repo_conventions
from agent_conventions.parser import parse_file


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.option("--repo", "repo_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--out", "out_path", required=True, type=click.Path(path_type=Path))
@click.option(
    "--pipelines-subdir",
    default=None,
    help="Subdir within --repo that contains pipeline folders (e.g., 'src/main/scala/pipelines').",
)
def extract(repo_path: Path, out_path: Path, pipelines_subdir: str | None) -> None:
    """Extract conventions from a target pipeline repo and write JSON."""
    conv = extract_repo(repo_path, pipelines_subdir=pipelines_subdir)
    write_repo_conventions(conv, out_path)
    click.echo(
        f"wrote {out_path} ({len(conv.pipelines)} pipelines, "
        f"{sum(len(p.nodes) for p in conv.pipelines)} nodes)"
    )


@cli.command("extract-pipeline")
@click.option("--folder", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--out", "out_path", required=False, type=click.Path(path_type=Path))
def extract_pipeline_cmd(folder: Path, out_path: Path | None) -> None:
    """Extract conventions from a single pipeline folder."""
    conv = extract_pipeline(folder)
    body = conv.model_dump_json(indent=2)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body)
        click.echo(f"wrote {out_path}")
    else:
        click.echo(body)


@cli.command("dump-tree")
@click.argument("scala_file", type=click.Path(exists=True, path_type=Path))
def dump_tree(scala_file: Path) -> None:
    """Print the tree-sitter parse tree of a .scala file. Debugging aid."""
    tree = parse_file(scala_file)

    def _walk(node, indent: int) -> None:
        click.echo("  " * indent + f"{node.type}  [{node.start_point}-{node.end_point}]")
        for c in node.children:
            _walk(c, indent + 1)

    _walk(tree.root_node, 0)


if __name__ == "__main__":
    cli()
