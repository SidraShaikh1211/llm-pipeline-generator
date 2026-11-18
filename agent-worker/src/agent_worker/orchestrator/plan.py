from __future__ import annotations

import json
import re
from enum import Enum

from pydantic import BaseModel, Field, ValidationError


class NodeStyle(str, Enum):
    A_SINGLE_NODE = "a_single_node"
    B_TYPED_UPSTREAM = "b_typed_upstream"
    C_REFERENCE_SOURCE = "c_reference_source"


class NodeRole(str, Enum):
    SOURCE = "source"
    INTERMEDIATE_FANOUT = "intermediate_fanout"
    BUCKET = "bucket"
    LEAF_TRANSFORM = "leaf_transform"
    PERSIST = "persist"


class FilePlan(BaseModel):
    path: str
    kind: str  # scala_node | scala_provider | scala_domain | ddl | test_spec | test_fixture
    style: NodeStyle | None = None
    role: NodeRole | None = None
    depends_on: list[str] = Field(default_factory=list)
    description: str


class DDLPlan(BaseModel):
    table_name: str
    table_schema: str = Field(alias="schema")
    columns: list[dict[str, str]] = Field(default_factory=list)
    partition_cols: list[str] = Field(default_factory=list)
    format: str | None = None
    location: str | None = None
    daily_volume_tier: int = 0

    model_config = {"populate_by_name": True}


class Plan(BaseModel):
    pipeline_name: str
    target_repo: str
    skills_required: list[str] = Field(default_factory=list)
    files: list[FilePlan] = Field(default_factory=list)
    ddls: list[DDLPlan] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    estimated_tokens: int = 0


class PlanParseError(Exception):
    pass


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_plan(text: str) -> Plan:
    """Parse a Plan from raw LLM output. Tolerates JSON inside fenced code blocks."""
    if not text or not text.strip():
        raise PlanParseError("planner returned empty output")

    candidates: list[str] = [text.strip()]
    for match in _FENCE_RE.finditer(text):
        body = match.group(1).strip()
        if body and body not in candidates:
            candidates.append(body)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = e
            continue
        try:
            return Plan.model_validate(data)
        except ValidationError as e:
            last_error = e
            continue

    raise PlanParseError(f"could not parse plan: {last_error}") from last_error
