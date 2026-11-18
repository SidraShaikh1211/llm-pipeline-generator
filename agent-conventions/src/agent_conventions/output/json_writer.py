from __future__ import annotations

from pathlib import Path

from agent_conventions.schemas.conventions import RepoConventions


def write_repo_conventions(conventions: RepoConventions, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(conventions.model_dump_json(indent=2))
