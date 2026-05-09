from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from saturn_eval import runner as runner_mod
from saturn_eval.dataset import load_case
from saturn_eval.runner import RunnerError, RunnerOptions, WorkerRunner


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "dataset"


def test_runner_calls_worker_with_expected_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = load_case(DATASET_DIR / "case_001_simple_workflow_node")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # Simulate worker writing files into the workspace
        workspace = Path(cmd[cmd.index("--workspace") + 1])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "FooNode.scala").write_text("class FooNode {}\n")
        summary = {
            "stage": "succeeded",
            "generated_files": ["FooNode.scala"],
            "error": None,
            "open_questions": [],
            "workspace_path": str(workspace),
            "tokens_used": 1,
            "job_id": "stub",
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(summary), "")

    monkeypatch.setattr(runner_mod.subprocess, "run", _fake_run)

    runner = WorkerRunner(
        options=RunnerOptions(worker_cli=["fake-worker-binary"])
    )
    output = runner.run(case, tmp_path)

    cmd = captured["cmd"]
    assert cmd[0] == "fake-worker-binary"
    assert "generate" in cmd
    assert "--mock-script" in cmd
    assert "--json-output" in cmd
    assert "--spec" in cmd
    assert output.generated_files == ["FooNode.scala"]
    assert output.output_dir.name == "workspace"


def test_runner_falls_back_to_walking_workspace_when_no_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = load_case(DATASET_DIR / "case_001_simple_workflow_node")

    def _fake_run(cmd, **kwargs):
        workspace = Path(cmd[cmd.index("--workspace") + 1])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "A.scala").write_text("x")
        (workspace / "sub").mkdir(exist_ok=True)
        (workspace / "sub" / "B.scala").write_text("y")
        return subprocess.CompletedProcess(cmd, 0, "(no json)", "")

    monkeypatch.setattr(runner_mod.subprocess, "run", _fake_run)
    runner = WorkerRunner(options=RunnerOptions(worker_cli=["x"]))
    output = runner.run(case, tmp_path)
    assert "A.scala" in output.generated_files
    assert "sub/B.scala" in output.generated_files


def test_runner_propagates_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = load_case(DATASET_DIR / "case_001_simple_workflow_node")
    monkeypatch.setattr(
        runner_mod.subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 5, "", "boom"),
    )
    with pytest.raises(RunnerError, match="errored unexpectedly"):
        WorkerRunner(options=RunnerOptions(worker_cli=["x"])).run(case, tmp_path)


def test_runner_missing_binary_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_mod.shutil, "which", lambda _: None)
    case = load_case(DATASET_DIR / "case_001_simple_workflow_node")
    with pytest.raises(RunnerError, match="not found on PATH"):
        WorkerRunner().run(case, tmp_path)


def test_runner_grades_failed_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code 1 (FAILED) is allowed; runner returns whatever the worker produced
    and lets the grader decide."""
    case = load_case(DATASET_DIR / "case_001_simple_workflow_node")

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "validation failed")

    monkeypatch.setattr(runner_mod.subprocess, "run", _fake_run)
    output = WorkerRunner(options=RunnerOptions(worker_cli=["x"])).run(case, tmp_path)
    assert output.generated_files == []
