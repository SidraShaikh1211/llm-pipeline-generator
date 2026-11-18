from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_worker.cli import cli


_PLAN_JSON = (
    '{"pipeline_name": "foo", "target_repo": "pipelines-reports", '
    '"files": [{"path": "FooNode.scala", "kind": "scala_node", '
    '"depends_on": [], "description": "stub"}]}'
)
_FOO_NODE = "package pipelines.foo\n\nclass FooNode {}\n"


def _write_mock_script(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "responses": [
                    {"text": _PLAN_JSON, "tokens_used": 100},
                    {
                        "tool_calls": [
                            {
                                "id": "w1",
                                "name": "write_file",
                                "arguments": {
                                    "path": "FooNode.scala",
                                    "content": _FOO_NODE,
                                },
                            }
                        ],
                        "tokens_used": 50,
                    },
                    {"text": "done", "tokens_used": 5},
                ]
            }
        )
    )


def test_version() -> None:
    result = CliRunner().invoke(cli, ["version"])
    assert result.exit_code == 0
    assert result.output.strip()


def test_generate_with_mock_script_succeeds(tmp_path: Path) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text("pipeline_name: foo\ntarget_repo: pipelines-reports\n")
    workspace = tmp_path / "workspace"
    mock = tmp_path / "mock.json"
    _write_mock_script(mock)

    result = CliRunner().invoke(
        cli,
        [
            "generate",
            "--spec",
            str(spec),
            "--workspace",
            str(workspace),
            "--mock-script",
            str(mock),
            "--json-output",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["stage"] == "succeeded"
    assert summary["generated_files"] == ["FooNode.scala"]
    assert (workspace / "FooNode.scala").read_text() == _FOO_NODE
    # Journal is dumped to the workspace
    journal = json.loads((workspace / "job_journal.json").read_text())
    assert journal["job"]["stage"] == "succeeded"
    types = [e["event_type"] for e in journal["events"]]
    assert "job.created" in types
    assert "stage.entered" in types
    assert "plan.parsed" in types
    assert "file.generated" in types
    assert "validation.passed" in types
    assert types[-1] == "job.succeeded"


def test_generate_human_output(tmp_path: Path) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text("pipeline_name: foo\ntarget_repo: pipelines-reports\n")
    workspace = tmp_path / "ws"
    mock = tmp_path / "mock.json"
    _write_mock_script(mock)

    result = CliRunner().invoke(
        cli,
        [
            "generate",
            "--spec",
            str(spec),
            "--workspace",
            str(workspace),
            "--mock-script",
            str(mock),
        ],
    )
    assert result.exit_code == 0
    assert "final stage: succeeded" in result.output
    assert "FooNode.scala" in result.output


def test_generate_failed_returns_exit_1(tmp_path: Path) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text("pipeline_name: foo\n")
    workspace = tmp_path / "ws"
    mock = tmp_path / "mock.json"
    mock.write_text(
        json.dumps({"responses": [{"text": "this is not json", "tokens_used": 10}]})
    )

    result = CliRunner().invoke(
        cli,
        [
            "generate",
            "--spec",
            str(spec),
            "--workspace",
            str(workspace),
            "--mock-script",
            str(mock),
            "--json-output",
        ],
    )
    assert result.exit_code == 1
    summary = json.loads(result.output)
    assert summary["stage"] == "failed"
    assert "plan parse error" in (summary["error"] or "")


def test_generate_paused_returns_exit_2(tmp_path: Path) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text("pipeline_name: foo\n")
    workspace = tmp_path / "ws"
    mock = tmp_path / "mock.json"
    plan_with_qs = json.dumps(
        {
            "pipeline_name": "foo",
            "target_repo": "pipelines-reports",
            "files": [],
            "open_questions": ["What is the schema?"],
        }
    )
    mock.write_text(json.dumps({"responses": [{"text": plan_with_qs, "tokens_used": 10}]}))

    result = CliRunner().invoke(
        cli,
        [
            "generate",
            "--spec",
            str(spec),
            "--workspace",
            str(workspace),
            "--mock-script",
            str(mock),
            "--json-output",
        ],
    )
    assert result.exit_code == 2
    summary = json.loads(result.output)
    assert summary["stage"] == "paused_for_user"
    assert "What is the schema?" in summary["open_questions"]


def test_generate_rejects_non_mapping_spec(tmp_path: Path) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text("- not a mapping\n")
    workspace = tmp_path / "ws"
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({"responses": []}))

    result = CliRunner().invoke(
        cli,
        [
            "generate",
            "--spec",
            str(spec),
            "--workspace",
            str(workspace),
            "--mock-script",
            str(mock),
        ],
    )
    assert result.exit_code != 0
    assert "spec must be a YAML mapping" in result.output
