from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from saturn_agent.persistence import InMemoryJobRepository


_TERMINAL = {"succeeded", "failed", "paused_for_user"}


def _wait_terminal(
    client: TestClient, job_id: str, *, timeout_s: float = 5.0
) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        body = r.json()
        if body["stage"] in _TERMINAL:
            return body
        time.sleep(0.05)
    pytest.fail(f"job {job_id} did not finish within {timeout_s}s; last={body}")


def test_create_returns_job_id_and_201(
    client: TestClient, mock_script: Path
) -> None:
    payload = {
        "spec": {"pipeline_name": "foo_reports"},
        "target_repo": "saturn-pipelines-reports",
        "mock_script_path": str(mock_script),
    }
    r = client.post("/api/jobs", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "job_id" in body
    assert body["target_repo"] == "saturn-pipelines-reports"
    assert body["user_id"] == "local-dev"


def test_create_then_get_runs_to_succeeded(
    client: TestClient, mock_script: Path
) -> None:
    payload = {
        "spec": {"pipeline_name": "foo_reports"},
        "target_repo": "saturn-pipelines-reports",
        "mock_script_path": str(mock_script),
    }
    job_id = client.post("/api/jobs", json=payload).json()["job_id"]
    body = _wait_terminal(client, job_id)
    assert body["stage"] == "succeeded", body
    assert body["error"] is None


def test_get_404_for_unknown(client: TestClient) -> None:
    r = client.get("/api/jobs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_list_jobs_returns_only_caller_jobs(
    client: TestClient, mock_script: Path
) -> None:
    initial = client.get("/api/jobs").json()
    assert initial == {"jobs": []}
    payload = {
        "spec": {"pipeline_name": "foo_reports"},
        "target_repo": "saturn-pipelines-reports",
        "mock_script_path": str(mock_script),
    }
    job_id = client.post("/api/jobs", json=payload).json()["job_id"]
    _wait_terminal(client, job_id)
    body = client.get("/api/jobs").json()
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["job_id"] == job_id


def test_replay_events_returns_full_tape(
    client: TestClient, mock_script: Path
) -> None:
    payload = {
        "spec": {"pipeline_name": "foo_reports"},
        "target_repo": "saturn-pipelines-reports",
        "mock_script_path": str(mock_script),
    }
    job_id = client.post("/api/jobs", json=payload).json()["job_id"]
    _wait_terminal(client, job_id)

    events = client.get(f"/api/jobs/{job_id}/events/replay").json()
    types = [e["event_type"] for e in events]
    assert "job.created" in types
    assert "stage.entered" in types
    assert "plan.parsed" in types
    assert "file.generated" in types
    assert types[-1] == "job.succeeded"


def test_create_with_missing_mock_script_returns_400(client: TestClient) -> None:
    payload = {
        "spec": {"pipeline_name": "foo"},
        "target_repo": "saturn-pipelines-reports",
        "mock_script_path": "/tmp/does/not/exist.json",
    }
    r = client.post("/api/jobs", json=payload)
    assert r.status_code == 400
    assert "mock script not found" in r.json()["detail"]


def test_sse_stream_replays_events(client: TestClient, mock_script: Path) -> None:
    payload = {
        "spec": {"pipeline_name": "foo_reports"},
        "target_repo": "saturn-pipelines-reports",
        "mock_script_path": str(mock_script),
    }
    job_id = client.post("/api/jobs", json=payload).json()["job_id"]
    _wait_terminal(client, job_id)

    # SSE endpoint also drains the existing tape and closes once terminal.
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    assert "job.created" in body
    assert "job.succeeded" in body
