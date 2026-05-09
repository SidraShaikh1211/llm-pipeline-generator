from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from saturn_agent.tools import test_local as tl_mod
from saturn_agent.tools.base import ToolContext, ToolError
from saturn_agent.tools.test_local import RunLocalTestsTool


def test_skips_when_sbt_missing(
    tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: None)
    out = RunLocalTestsTool().invoke({}, tool_ctx)
    assert "sbt not installed" in out


def test_runs_full_suite_when_specs_omitted(
    tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/usr/local/bin/sbt")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "[info] All tests passed", "")

    monkeypatch.setattr(tl_mod.subprocess, "run", _fake_run)
    out = RunLocalTestsTool().invoke({}, tool_ctx)
    assert "sbt test ok" in out
    assert captured["cmd"] == ["/usr/local/bin/sbt", "test"]


def test_runs_specs_via_test_only(
    tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(tl_mod.subprocess, "run", _fake_run)
    RunLocalTestsTool().invoke({"specs": ["FooSpec", "BarSpec"]}, tool_ctx)
    assert captured["cmd"][0] == "/bin/sbt"
    assert captured["cmd"][1].startswith("testOnly ")
    assert "*FooSpec*" in captured["cmd"][1]
    assert "*BarSpec*" in captured["cmd"][1]


def test_failure_surfaces_output(
    tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    monkeypatch.setattr(
        tl_mod.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, "[error] FooSpec.test1 FAILED", ""
        ),
    )
    out = RunLocalTestsTool().invoke({}, tool_ctx)
    assert out.startswith("sbt test failed")
    assert "FAILED" in out


def test_timeout_returns_message(
    tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")

    def _raises(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="sbt", timeout=1)

    monkeypatch.setattr(tl_mod.subprocess, "run", _raises)
    out = RunLocalTestsTool().invoke({}, tool_ctx)
    assert "timed out" in out


def test_cwd_must_be_in_workspace(
    tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    with pytest.raises(ToolError, match="escapes workspace"):
        RunLocalTestsTool().invoke({"cwd": "../../etc"}, tool_ctx)


def test_cwd_must_be_a_directory(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    (workspace / "afile").write_text("x")
    with pytest.raises(ToolError, match="not a directory"):
        RunLocalTestsTool().invoke({"cwd": "afile"}, tool_ctx)


def test_specs_must_be_strings(tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    with pytest.raises(ToolError, match="list of strings"):
        RunLocalTestsTool().invoke({"specs": [123]}, tool_ctx)


def test_default_cwd_prefers_cloned_sbt_repo(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the cloned target repo has build.sbt, sbt must be invoked from
    that dir — running from the workspace root makes sbt error out with
    'Neither build.sbt nor a project directory'."""
    repo_dir = workspace / "repos" / "saturn-pipelines-reports"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "build.sbt").write_text("scalaVersion := \"2.13.14\"\n")

    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(tl_mod.subprocess, "run", _fake_run)
    RunLocalTestsTool().invoke({}, tool_ctx)
    assert captured["cwd"] == str(repo_dir.resolve())


def test_default_cwd_falls_back_to_workspace_when_no_build_sbt(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(tl_mod.subprocess, "run", _fake_run)
    RunLocalTestsTool().invoke({}, tool_ctx)
    assert captured["cwd"] == str(workspace.resolve())


def test_spec_paths_are_converted_to_class_globs(
    tool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator passes file paths like
    'repos/<repo>/src/test/scala/saturn/foo/FooSpec.scala' — sbt's testOnly
    matches class names, so we must reduce each path to its basename."""
    monkeypatch.setattr(tl_mod.shutil, "which", lambda _name: "/bin/sbt")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, **_k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(tl_mod.subprocess, "run", _fake_run)
    RunLocalTestsTool().invoke(
        {
            "specs": [
                "repos/saturn-pipelines-reports/src/test/scala/saturn/foo/FooSpec.scala",
                "saturn.bar.BarSpec",
                "BazSpec",
            ]
        },
        tool_ctx,
    )
    cmd = captured["cmd"][1]
    assert "*FooSpec*" in cmd
    assert "*BarSpec*" in cmd
    assert "*BazSpec*" in cmd
    assert "/" not in cmd
    assert ".scala" not in cmd
