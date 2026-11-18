from __future__ import annotations

import json
from pathlib import Path

from agent_worker.conventions.loader import load_conventions, pipeline_name_from_spec


_SAMPLE = {
    "repo_name": "pipelines-reports",
    "extracted_at": "2026-05-01T00:00:00Z",
    "common_imports": ["import pipelines.framework.Reference"],
    "pipelines": [
        {"pipeline_name": "foo_reports", "folder_path": "foo_reports"},
        {"pipeline_name": "bar_reports", "folder_path": "bar_reports"},
    ],
}


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "conventions.json"
    p.write_text(json.dumps(_SAMPLE))
    return p


def test_returns_full_when_no_pipeline_name(tmp_path: Path) -> None:
    out = load_conventions(_write(tmp_path), None)
    assert out is not None
    assert {p["pipeline_name"] for p in out["pipelines"]} == {
        "foo_reports",
        "bar_reports",
    }


def test_slice_when_pipeline_matches(tmp_path: Path) -> None:
    out = load_conventions(_write(tmp_path), "foo_reports")
    assert out is not None
    assert [p["pipeline_name"] for p in out["pipelines"]] == ["foo_reports"]
    assert out["repo_name"] == "pipelines-reports"  # top-level keys preserved


def test_full_when_pipeline_does_not_match(tmp_path: Path) -> None:
    out = load_conventions(_write(tmp_path), "brand_new_pipeline")
    assert out is not None
    assert len(out["pipelines"]) == 2  # falls back to full repo conventions


def test_returns_none_when_path_missing(tmp_path: Path) -> None:
    out = load_conventions(tmp_path / "nope.json", "foo")
    assert out is None


def test_returns_none_when_path_is_none() -> None:
    assert load_conventions(None, "foo") is None


def test_returns_none_on_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json {")
    assert load_conventions(p) is None


def test_returns_none_when_root_is_not_dict(tmp_path: Path) -> None:
    p = tmp_path / "weird.json"
    p.write_text("[]")
    assert load_conventions(p) is None


def test_pipeline_name_from_spec_prefers_pipeline_name() -> None:
    assert pipeline_name_from_spec({"pipeline_name": "x", "pipeline_domain": "y"}) == "x"


def test_pipeline_name_from_spec_falls_back_to_domain() -> None:
    assert pipeline_name_from_spec({"pipeline_domain": "y"}) == "y"


def test_pipeline_name_from_spec_none_when_neither() -> None:
    assert pipeline_name_from_spec({"other": "z"}) is None
