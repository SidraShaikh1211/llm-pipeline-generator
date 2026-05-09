from __future__ import annotations

from pathlib import Path

import pytest

from saturn_eval.dataset import DatasetError, load_case, load_dataset


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "dataset"


def test_load_stub_case_001() -> None:
    case = load_case(DATASET_DIR / "case_001_simple_workflow_node")
    assert case.case_id == "case_001_simple_workflow_node"
    assert case.spec["pipeline_name"] == "foo_reports"
    assert case.grading.must_compile is True
    assert case.grading.files_must_exist == ["FooNode.scala", "FooRocketProvider.scala"]
    assert case.grading.provider is not None
    assert case.grading.provider.expected_node_ids == ["foo_workflow"]


def test_load_dataset_picks_up_case() -> None:
    cases = load_dataset(DATASET_DIR)
    ids = [c.case_id for c in cases]
    assert "case_001_simple_workflow_node" in ids


def test_load_case_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(DatasetError):
        load_case(tmp_path / "does_not_exist")


def test_load_case_missing_grading(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / "spec.yaml").write_text("pipeline_name: x\n")
    with pytest.raises(DatasetError):
        load_case(case_dir)


def test_load_case_invalid_spec(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_y"
    case_dir.mkdir()
    (case_dir / "spec.yaml").write_text("- not a mapping\n")
    (case_dir / "grading.yaml").write_text("must_compile: true\n")
    with pytest.raises(DatasetError):
        load_case(case_dir)
