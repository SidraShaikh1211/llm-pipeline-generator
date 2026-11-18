from __future__ import annotations

import pytest

from agent_worker.orchestrator.plan import Plan, PlanParseError, parse_plan


_VALID = """
{
  "pipeline_name": "foo_reports",
  "target_repo": "pipelines-reports",
  "skills_required": ["pipeline-builder"],
  "files": [
    {
      "path": "src/main/scala/pipelines/foo/FooNode.scala",
      "kind": "scala_node",
      "style": "a_single_node",
      "depends_on": [],
      "description": "Read cdl_d_party, project to (party_id, report_code, report_description)."
    }
  ],
  "ddls": [],
  "assumptions": ["entity_code = foo"],
  "open_questions": [],
  "estimated_tokens": 1234
}
"""


def test_parse_plan_raw_json() -> None:
    plan = parse_plan(_VALID)
    assert plan.pipeline_name == "foo_reports"
    assert plan.files[0].path.endswith("FooNode.scala")


def test_parse_plan_inside_code_fence() -> None:
    text = f"Here is the plan:\n```json\n{_VALID}\n```\nEnd."
    plan = parse_plan(text)
    assert plan.pipeline_name == "foo_reports"


def test_parse_plan_inside_unlabeled_fence() -> None:
    text = f"```\n{_VALID}\n```"
    plan = parse_plan(text)
    assert plan.pipeline_name == "foo_reports"


def test_parse_plan_empty() -> None:
    with pytest.raises(PlanParseError, match="empty"):
        parse_plan("")


def test_parse_plan_malformed_json() -> None:
    with pytest.raises(PlanParseError):
        parse_plan("{not valid json")


def test_parse_plan_missing_required_field() -> None:
    with pytest.raises(PlanParseError):
        parse_plan('{"pipeline_name": "x"}')  # target_repo missing


def test_plan_open_questions_default_empty() -> None:
    plan = parse_plan(_VALID)
    assert plan.open_questions == []
