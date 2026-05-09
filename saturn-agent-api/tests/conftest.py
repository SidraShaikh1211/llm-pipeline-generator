from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from saturn_agent.persistence import InMemoryJobRepository

from saturn_api.config import Settings
from saturn_api.main import create_app


_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKER_SKILLS = (_REPO_ROOT.parent / "saturn-agent-worker" / "skills").resolve()


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    root.mkdir()
    return root


@pytest.fixture
def settings(workspace_root: Path) -> Settings:
    return Settings(
        workspace_root=workspace_root,
        skills_root=_WORKER_SKILLS,
        max_tokens_per_job=10_000,
    )


@pytest.fixture
def repository() -> InMemoryJobRepository:
    return InMemoryJobRepository()


@pytest.fixture
def client(settings: Settings, repository: InMemoryJobRepository) -> TestClient:
    app = create_app(settings=settings, repository=repository)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_script(tmp_path: Path) -> Path:
    """A simple successful mock script: planner emits a single-file plan,
    generator writes the file, validation skips, testing skips."""
    plan = json.dumps(
        {
            "pipeline_name": "foo_reports",
            "target_repo": "saturn-pipelines-reports",
            "files": [
                {
                    "path": "FooNode.scala",
                    "kind": "scala_node",
                    "depends_on": [],
                    "description": "stub",
                }
            ],
        }
    )
    body = {
        "responses": [
            {"text": plan, "tokens_used": 100},
            {
                "tool_calls": [
                    {
                        "id": "w1",
                        "name": "write_file",
                        "arguments": {
                            "path": "FooNode.scala",
                            "content": "package x\nclass FooNode {}\n",
                        },
                    }
                ],
                "tokens_used": 50,
            },
            {"text": "done", "tokens_used": 5},
        ]
    }
    path = tmp_path / "mock.json"
    path.write_text(json.dumps(body))
    return path
