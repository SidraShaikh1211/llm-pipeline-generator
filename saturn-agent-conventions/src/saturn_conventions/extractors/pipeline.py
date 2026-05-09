from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from tree_sitter import Node, Tree

from saturn_conventions.extractors.node import extract_node
from saturn_conventions.extractors.provider import extract_provider, is_provider
from saturn_conventions.parser import parse_file
from saturn_conventions.schemas.conventions import (
    NodeConvention,
    PipelineConvention,
    ProviderConvention,
    RepoConventions,
)


class ExtractionError(Exception):
    pass


def extract_pipeline(folder: Path, repo_root: Path | None = None) -> PipelineConvention:
    folder = folder.resolve()
    if not folder.is_dir():
        raise ExtractionError(f"not a directory: {folder}")

    pipeline_name = folder.name
    rel_folder = (
        folder.relative_to(repo_root.resolve()) if repo_root else Path(folder.name)
    )

    nodes: list[NodeConvention] = []
    provider: ProviderConvention | None = None

    for scala_file in sorted(folder.rglob("*.scala")):
        rel = scala_file.relative_to(repo_root.resolve()) if repo_root else scala_file.name
        source = scala_file.read_bytes()
        tree = parse_file(scala_file)
        for class_def in _top_level_classes(tree):
            if is_provider(class_def, source):
                if provider is not None:
                    raise ExtractionError(
                        f"multiple providers in pipeline {pipeline_name}: "
                        f"{provider.name} and {class_def.text.decode('utf-8', 'replace').splitlines()[0]}"
                    )
                provider = extract_provider(class_def, source, Path(rel))
            elif _looks_like_node(class_def, source):
                nodes.append(extract_node(class_def, source, Path(rel)))

    return PipelineConvention(
        pipeline_name=pipeline_name,
        folder_path=str(rel_folder),
        provider=provider,
        nodes=nodes,
        domain=None,
    )


def extract_repo(repo_path: Path, pipelines_subdir: str | None = None) -> RepoConventions:
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise ExtractionError(f"not a directory: {repo_path}")

    base = repo_path / pipelines_subdir if pipelines_subdir else repo_path
    if not base.is_dir():
        raise ExtractionError(f"pipelines subdir not found: {base}")

    pipelines: list[PipelineConvention] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not any(child.rglob("*.scala")):
            continue
        pipelines.append(extract_pipeline(child, repo_root=repo_path))

    return RepoConventions(
        repo_name=repo_path.name,
        extracted_at=datetime.now(UTC),
        pipelines=pipelines,
        common_imports=_compute_common_imports(repo_path, threshold=0.8),
    )


def _top_level_classes(tree: Tree) -> list[Node]:
    out: list[Node] = []
    for child in tree.root_node.children:
        if child.type == "class_definition":
            out.append(child)
    return out


def _looks_like_node(class_def: Node, source: bytes) -> bool:
    """A class is a Node if its extends_clause references Node[T] or has 'Node' identifier."""
    ec = next((c for c in class_def.children if c.type == "extends_clause"), None)
    if ec is None:
        return False
    for c in ec.children:
        if c.type == "type_identifier":
            if c.text.decode("utf-8") == "Node":
                return True
        if c.type == "generic_type":
            head = next((g for g in c.children if g.type == "type_identifier"), None)
            if head is not None and head.text.decode("utf-8") == "Node":
                return True
    return False


def _compute_common_imports(repo_path: Path, threshold: float) -> list[str]:
    """Imports that appear in >= threshold fraction of .scala files."""
    files = list(repo_path.rglob("*.scala"))
    if not files:
        return []
    counter: Counter[str] = Counter()
    for f in files:
        seen_in_file: set[str] = set()
        for line in f.read_text(errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") and stripped not in seen_in_file:
                seen_in_file.add(stripped)
        counter.update(seen_in_file)
    cutoff = max(1, int(len(files) * threshold))
    return sorted(imp for imp, n in counter.items() if n >= cutoff)
