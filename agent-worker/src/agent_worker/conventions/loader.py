from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_conventions(
    conventions_path: Path | None,
    pipeline_name: str | None = None,
) -> dict[str, Any] | None:
    """Load the conventions JSON, optionally sliced to a single pipeline.

    Returns None if conventions_path is unset, missing, or unreadable. When a
    pipeline_name is provided AND a matching pipeline exists, returns a slice
    containing only that pipeline. When no match exists (e.g., the agent is
    creating a brand-new pipeline), returns the full repo blob so the prompt
    still has structural conventions to anchor on.
    """
    if conventions_path is None or not conventions_path.is_file():
        return None
    try:
        data = json.loads(conventions_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    if not pipeline_name:
        return data

    pipelines = data.get("pipelines", []) or []
    matches = [
        p
        for p in pipelines
        if isinstance(p, dict) and p.get("pipeline_name") == pipeline_name
    ]
    if not matches:
        return data
    return {**{k: v for k, v in data.items() if k != "pipelines"}, "pipelines": matches}


def pipeline_name_from_spec(spec: dict[str, Any]) -> str | None:
    name = spec.get("pipeline_name") or spec.get("pipeline_domain")
    return str(name) if name else None
