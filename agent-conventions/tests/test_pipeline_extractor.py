from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_conventions.extractors.pipeline import (
    ExtractionError,
    extract_pipeline,
    extract_repo,
)
from agent_conventions.output.json_writer import write_repo_conventions


REPO_FIXTURE = Path(__file__).parent / "fixtures" / "pipelines-reports"


def test_extract_pipeline_collects_nodes_and_provider() -> None:
    pl = extract_pipeline(REPO_FIXTURE / "foo_reports", repo_root=REPO_FIXTURE)
    assert pl.pipeline_name == "foo_reports"
    assert pl.provider is not None
    assert pl.provider.name == "FooRocketProvider"
    assert {n.class_name for n in pl.nodes} == {"FooNode", "FooPersistNode"}
    foo = next(n for n in pl.nodes if n.class_name == "FooNode")
    assert foo.references == [("cdl", "cdl_d_party")]


def test_extract_repo_walks_all_pipelines() -> None:
    repo = extract_repo(REPO_FIXTURE)
    assert repo.repo_name == "pipelines-reports"
    assert {p.pipeline_name for p in repo.pipelines} == {"foo_reports"}
    assert repo.pipelines[0].provider is not None


def test_extract_repo_writes_valid_json(tmp_path: Path) -> None:
    repo = extract_repo(REPO_FIXTURE)
    out = tmp_path / "conventions.json"
    write_repo_conventions(repo, out)
    data = json.loads(out.read_text())
    assert data["repo_name"] == "pipelines-reports"
    assert data["pipelines"][0]["pipeline_name"] == "foo_reports"
    assert data["pipelines"][0]["provider"]["finish_called"] is True


def test_extract_pipeline_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError):
        extract_pipeline(tmp_path / "does_not_exist")


def test_extract_repo_skips_pipelines_without_scala(tmp_path: Path) -> None:
    repo_dir = tmp_path / "empty_repo"
    repo_dir.mkdir()
    (repo_dir / "no_scala_here").mkdir()
    (repo_dir / "no_scala_here" / "README.md").write_text("hi")
    repo = extract_repo(repo_dir)
    assert repo.pipelines == []
