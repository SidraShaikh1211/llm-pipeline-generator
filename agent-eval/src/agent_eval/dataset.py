from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ProviderGrading(BaseModel):
    must_call_finish: bool | None = None
    expected_node_ids: list[str] = Field(default_factory=list)
    expected_global_args: list[str] = Field(default_factory=list)
    expected_node_arg_pairs: list[tuple[str, str]] = Field(default_factory=list)


class DDLGrading(BaseModel):
    expected: bool = False
    table_name: str | None = None
    expected_columns: list[str] = Field(default_factory=list)
    expected_partition_cols: list[str] = Field(default_factory=list)


class Grading(BaseModel):
    must_compile: bool = False
    must_pass_convention_checks: bool = False
    files_must_exist: list[str] = Field(default_factory=list)
    test_pass_rate_minimum: float | None = None
    provider: ProviderGrading | None = None
    ddl: DDLGrading | None = None


class Case(BaseModel):
    case_id: str
    case_dir: Path
    spec: dict[str, Any]
    grading: Grading
    expected_files_dir: Path
    source_pr: str | None = None

    model_config = {"arbitrary_types_allowed": True}


class DatasetError(Exception):
    pass


def load_case(case_dir: Path) -> Case:
    case_dir = case_dir.resolve()
    if not case_dir.is_dir():
        raise DatasetError(f"case dir does not exist: {case_dir}")

    spec_path = case_dir / "spec.yaml"
    grading_path = case_dir / "grading.yaml"
    expected_files_dir = case_dir / "expected_files"

    for required in (spec_path, grading_path):
        if not required.is_file():
            raise DatasetError(f"missing required file: {required}")

    spec = yaml.safe_load(spec_path.read_text())
    if not isinstance(spec, dict):
        raise DatasetError(f"spec.yaml must be a mapping, got {type(spec).__name__}")

    grading_raw = yaml.safe_load(grading_path.read_text()) or {}
    if not isinstance(grading_raw, dict):
        raise DatasetError("grading.yaml must be a mapping")
    grading = Grading.model_validate(grading_raw)

    source_pr_path = case_dir / "source_pr.txt"
    source_pr = source_pr_path.read_text().strip() if source_pr_path.is_file() else None

    return Case(
        case_id=case_dir.name,
        case_dir=case_dir,
        spec=spec,
        grading=grading,
        expected_files_dir=expected_files_dir,
        source_pr=source_pr,
    )


def load_dataset(dataset_dir: Path) -> list[Case]:
    dataset_dir = dataset_dir.resolve()
    if not dataset_dir.is_dir():
        raise DatasetError(f"dataset dir does not exist: {dataset_dir}")

    cases: list[Case] = []
    for child in sorted(dataset_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "spec.yaml").is_file():
            continue
        cases.append(load_case(child))
    return cases
