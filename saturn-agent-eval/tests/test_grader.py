from __future__ import annotations

from pathlib import Path

import pytest

from saturn_eval.dataset import Case, Grading
from saturn_eval.grader import GateStatus, Grader, WorkerOutput


def make_case(grading: Grading, tmp_path: Path) -> Case:
    return Case(
        case_id="dummy",
        case_dir=tmp_path,
        spec={"pipeline_name": "dummy"},
        grading=grading,
        expected_files_dir=tmp_path / "expected_files",
    )


def test_files_must_exist_passes(tmp_path: Path) -> None:
    case = make_case(
        Grading(files_must_exist=["A.scala", "B.scala"]),
        tmp_path,
    )
    out = WorkerOutput(output_dir=tmp_path, generated_files=["A.scala", "B.scala", "C.scala"])
    score = Grader().grade(case, out)
    assert len(score.gates) == 1
    assert score.gates[0].status == GateStatus.PASSED


def test_files_must_exist_fails_lists_missing(tmp_path: Path) -> None:
    case = make_case(Grading(files_must_exist=["A.scala", "B.scala"]), tmp_path)
    out = WorkerOutput(output_dir=tmp_path, generated_files=["A.scala"])
    score = Grader().grade(case, out)
    assert score.gates[0].status == GateStatus.FAILED
    assert "B.scala" in (score.gates[0].detail or "")


def test_compile_unknown_is_not_implemented(tmp_path: Path) -> None:
    case = make_case(Grading(must_compile=True), tmp_path)
    out = WorkerOutput(output_dir=tmp_path, generated_files=[])
    score = Grader().grade(case, out)
    assert score.gates[0].status == GateStatus.NOT_IMPLEMENTED


def test_compile_failed(tmp_path: Path) -> None:
    case = make_case(Grading(must_compile=True), tmp_path)
    out = WorkerOutput(output_dir=tmp_path, generated_files=[], compile_ok=False)
    score = Grader().grade(case, out)
    assert score.gates[0].status == GateStatus.FAILED


def test_compile_passed(tmp_path: Path) -> None:
    case = make_case(Grading(must_compile=True), tmp_path)
    out = WorkerOutput(output_dir=tmp_path, generated_files=[], compile_ok=True)
    score = Grader().grade(case, out)
    assert score.gates[0].status == GateStatus.PASSED


def test_test_pass_rate_below_minimum(tmp_path: Path) -> None:
    case = make_case(Grading(test_pass_rate_minimum=0.8), tmp_path)
    out = WorkerOutput(output_dir=tmp_path, generated_files=[], test_pass_rate=0.5)
    score = Grader().grade(case, out)
    assert score.gates[0].status == GateStatus.FAILED


def test_aggregate_score_partial(tmp_path: Path) -> None:
    case = make_case(
        Grading(must_compile=True, files_must_exist=["A.scala"]),
        tmp_path,
    )
    out = WorkerOutput(
        output_dir=tmp_path,
        generated_files=["A.scala"],
        compile_ok=False,
    )
    score = Grader().grade(case, out)
    assert score.passed == 1
    assert score.total == 2
    assert score.score == pytest.approx(0.5)
