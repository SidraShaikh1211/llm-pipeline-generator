from __future__ import annotations

import pytest

from agent_worker.agent.prompt_builder import PromptBuilder


def test_planner_prompt_includes_spec_json() -> None:
    pb = PromptBuilder()
    out = pb.build_planner_prompt(spec={"pipeline_name": "foo_reports"})
    assert "Pipeline Planner" in out
    assert '"pipeline_name": "foo_reports"' in out


def test_planner_prompt_with_conventions() -> None:
    pb = PromptBuilder()
    out = pb.build_planner_prompt(
        spec={"a": 1}, conventions={"repo_name": "pipelines-reports"}
    )
    assert "Conventions slice" in out
    assert "pipelines-reports" in out


def test_generator_prompt_includes_skill_and_plan() -> None:
    pb = PromptBuilder()
    out = pb.build_generator_prompt(
        skill_text="# fake skill",
        references=None,
        conventions=None,
        plan={"pipeline_name": "foo"},
        current_file={"path": "FooNode.scala", "kind": "scala_node"},
    )
    assert "# fake skill" in out
    assert "FooNode.scala" in out


def test_repair_prompt_lists_files() -> None:
    pb = PromptBuilder()
    out = pb.build_repair_prompt(
        errors="scalac: not found", affected_files=["A.scala", "B.scala"]
    )
    assert "scalac: not found" in out
    assert "- A.scala" in out
    assert "- B.scala" in out


def test_unknown_kind_raises_keyerror() -> None:
    pb = PromptBuilder(versions={"planner": "v1"})
    with pytest.raises(KeyError, match="generator"):
        pb._load("generator")


def test_missing_version_file_raises(tmp_path) -> None:
    pb = PromptBuilder(prompts_dir=tmp_path, versions={"planner": "v999"})
    with pytest.raises(FileNotFoundError):
        pb.build_planner_prompt(spec={})
