from __future__ import annotations

import pytest

from saturn_agent.orchestrator.dag import (
    CycleError,
    DanglingDepError,
    DuplicatePathError,
    validate_and_sort,
)
from saturn_agent.orchestrator.plan import FilePlan, Plan


def _file(path: str, depends_on: list[str] | None = None) -> FilePlan:
    return FilePlan(
        path=path,
        kind="scala_node",
        depends_on=depends_on or [],
        description="stub",
    )


def _plan(files: list[FilePlan]) -> Plan:
    return Plan(
        pipeline_name="foo_reports",
        target_repo="saturn-pipelines-reports",
        files=files,
    )


def test_topo_sort_chain() -> None:
    # Scrambled order: C, A, B with chain A -> B -> C.
    files = [
        _file("C.scala", depends_on=["B.scala"]),
        _file("A.scala"),
        _file("B.scala", depends_on=["A.scala"]),
    ]
    sorted_plan = validate_and_sort(_plan(files))
    assert [f.path for f in sorted_plan.files] == ["A.scala", "B.scala", "C.scala"]


def test_topo_sort_diamond_preserves_sibling_order() -> None:
    # A -> {B, C} -> D. Insertion-stable Kahn's keeps B before C since B
    # appears before C in the input.
    files = [
        _file("A.scala"),
        _file("B.scala", depends_on=["A.scala"]),
        _file("C.scala", depends_on=["A.scala"]),
        _file("D.scala", depends_on=["B.scala", "C.scala"]),
    ]
    sorted_plan = validate_and_sort(_plan(files))
    paths = [f.path for f in sorted_plan.files]
    assert paths[0] == "A.scala"
    assert paths[-1] == "D.scala"
    assert paths.index("B.scala") < paths.index("C.scala")


def test_topo_sort_single_node() -> None:
    files = [_file("Solo.scala")]
    sorted_plan = validate_and_sort(_plan(files))
    assert [f.path for f in sorted_plan.files] == ["Solo.scala"]


def test_topo_sort_independent_nodes_preserve_emit_order() -> None:
    files = [_file("X.scala"), _file("Y.scala"), _file("Z.scala")]
    sorted_plan = validate_and_sort(_plan(files))
    assert [f.path for f in sorted_plan.files] == ["X.scala", "Y.scala", "Z.scala"]


def test_cycle_raises_cycle_error() -> None:
    files = [
        _file("A.scala", depends_on=["B.scala"]),
        _file("B.scala", depends_on=["A.scala"]),
    ]
    with pytest.raises(CycleError) as excinfo:
        validate_and_sort(_plan(files))
    msg = str(excinfo.value)
    assert "A.scala" in msg
    assert "B.scala" in msg


def test_dangling_dep_raises() -> None:
    files = [_file("A.scala", depends_on=["Ghost.scala"])]
    with pytest.raises(DanglingDepError) as excinfo:
        validate_and_sort(_plan(files))
    assert "Ghost.scala" in str(excinfo.value)
    assert "A.scala" in str(excinfo.value)


def test_duplicate_path_raises() -> None:
    files = [_file("Dup.scala"), _file("Dup.scala")]
    with pytest.raises(DuplicatePathError) as excinfo:
        validate_and_sort(_plan(files))
    assert "Dup.scala" in str(excinfo.value)


def test_self_loop_is_a_cycle() -> None:
    files = [_file("A.scala", depends_on=["A.scala"])]
    with pytest.raises(CycleError):
        validate_and_sort(_plan(files))


def test_returns_new_plan_does_not_mutate_input() -> None:
    files = [
        _file("B.scala", depends_on=["A.scala"]),
        _file("A.scala"),
    ]
    original = _plan(files)
    original_order = [f.path for f in original.files]
    sorted_plan = validate_and_sort(original)
    assert [f.path for f in original.files] == original_order
    assert [f.path for f in sorted_plan.files] == ["A.scala", "B.scala"]
