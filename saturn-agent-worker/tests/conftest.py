from __future__ import annotations

from pathlib import Path

import pytest

from saturn_agent.tools.base import ToolContext


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"
REFERENCES_ROOT = SKILLS_ROOT / "references"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "repos").mkdir()
    (ws / "outputs").mkdir()
    return ws


@pytest.fixture
def tool_ctx(workspace: Path) -> ToolContext:
    return ToolContext(
        job_id="job-1",
        workspace_path=workspace,
        target_repo_path=workspace / "repos" / "saturn-pipelines-reports",
        devbox_host=None,
        user_id="local-dev",
        skills_root=SKILLS_ROOT,
        references_root=REFERENCES_ROOT,
        conventions_path=None,
    )


@pytest.fixture
def conventions_path(tmp_path: Path) -> Path:
    p = tmp_path / "conventions.json"
    p.write_text(
        """
        {
          "repo_name": "saturn-pipelines-reports",
          "extracted_at": "2026-05-01T00:00:00Z",
          "common_imports": [],
          "pipelines": [
            {
              "pipeline_name": "foo_reports",
              "folder_path": "foo_reports",
              "provider": null,
              "nodes": [],
              "domain": null
            },
            {
              "pipeline_name": "bar_reports",
              "folder_path": "bar_reports",
              "provider": null,
              "nodes": [],
              "domain": null
            }
          ]
        }
        """.strip()
    )
    return p
