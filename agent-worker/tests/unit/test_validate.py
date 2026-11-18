from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_worker.tools import validate as validate_mod
from agent_worker.tools.base import ToolContext, ToolError
from agent_worker.tools.validate import (
    ConventionCheckTool,
    ScalacTool,
    ScalafmtTool,
    list_rules,
)


# ---------- scalafmt / scalac (subprocess mocking) ----------


def _seed_scala(workspace: Path) -> str:
    rel = "src/main/scala/Foo.scala"
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("class Foo {}\n")
    return rel


def test_scalafmt_skips_when_binary_missing(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: None)
    out = ScalafmtTool().invoke({"paths": [rel]}, tool_ctx)
    assert "not installed" in out


def test_scalafmt_runs_when_present(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    monkeypatch.setattr(
        validate_mod.shutil, "which", lambda _name: "/usr/local/bin/scalafmt"
    )
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate_mod.subprocess, "run", _fake_run)
    out = ScalafmtTool().invoke({"paths": [rel]}, tool_ctx)
    assert "ok" in out
    assert captured["cmd"][0] == "/usr/local/bin/scalafmt"
    assert captured["cmd"][1].endswith(rel)


def test_scalafmt_failure_surfaces_output(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: "/bin/scalafmt")
    monkeypatch.setattr(
        validate_mod.subprocess,
        "run",
        lambda cmd, **_k: subprocess.CompletedProcess(cmd, 1, "stdout-msg", "stderr-msg"),
    )
    out = ScalafmtTool().invoke({"paths": [rel]}, tool_ctx)
    assert "failed" in out
    assert "stderr-msg" in out


def test_scalafmt_requires_paths(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="non-empty 'paths' list"):
        ScalafmtTool().invoke({}, tool_ctx)


def test_scalac_skips_when_binary_missing(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: None)
    out = ScalacTool().invoke({"paths": [rel]}, tool_ctx)
    assert "not installed" in out


def test_scalac_passes_classpath(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: "/bin/scalac")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate_mod.subprocess, "run", _fake_run)
    out = ScalacTool().invoke(
        {"paths": [rel], "classpath": "/cp/pipelines.jar"}, tool_ctx
    )
    assert "ok" in out
    assert "-classpath" in captured["cmd"]
    assert "/cp/pipelines.jar" in captured["cmd"]


def test_scalac_compile_error(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: "/bin/scalac")
    monkeypatch.setattr(
        validate_mod.subprocess,
        "run",
        lambda cmd, **_k: subprocess.CompletedProcess(cmd, 1, "", "Foo.scala:3: error: not found"),
    )
    out = ScalacTool().invoke({"paths": [rel]}, tool_ctx)
    assert "failed" in out
    assert "not found" in out


# ---------- sbt-compile fallback for scalac ----------


def _seed_sbt_repo(workspace: Path, repo_name: str = "pipelines-reports") -> Path:
    """Create a clone-shaped repo with build.sbt + .scalafmt.conf so the
    ScalacTool/ScalafmtTool helpers detect it as an sbt project."""
    repo_dir = workspace / "repos" / repo_name
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "build.sbt").write_text("scalaVersion := \"2.13.14\"\n")
    (repo_dir / ".scalafmt.conf").write_text("version = \"3.7.17\"\n")
    return repo_dir


def test_scalac_runs_sbt_when_build_sbt_present(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    _seed_sbt_repo(workspace)

    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: f"/bin/{_name}")
    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "[info] done compiling\n", "")

    monkeypatch.setattr(validate_mod.subprocess, "run", _fake_run)
    out = ScalacTool().invoke({"paths": [rel]}, tool_ctx)

    # scalac return string still leads with "scalac ok" so stage_validating's
    # string match keeps working.
    assert out.startswith("scalac ok"), out
    assert "sbt compile" in out
    cmd = captured["cmd"]
    assert isinstance(cmd, list) and cmd[:2] == ["sbt", "-batch"]
    assert "compile" in cmd
    assert captured["cwd"] == str(workspace / "repos" / "pipelines-reports")


def test_scalac_sbt_failure_keeps_scalac_failed_prefix(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    _seed_sbt_repo(workspace)

    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: f"/bin/{_name}")
    monkeypatch.setattr(
        validate_mod.subprocess,
        "run",
        lambda cmd, **_k: subprocess.CompletedProcess(
            cmd,
            1,
            "[error] /tmp/Foo.scala:3:11: value apache is not a member of org\n[error] one error found\n",
            "",
        ),
    )
    out = ScalacTool().invoke({"paths": [rel]}, tool_ctx)
    assert out.startswith("scalac failed"), out
    assert "value apache is not a member of org" in out


def test_scalac_falls_back_to_raw_scalac_without_build_sbt(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _seed_scala(workspace)
    # No build.sbt under target_repo_path, so the sbt branch must NOT trigger.

    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: f"/bin/{_name}")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate_mod.subprocess, "run", _fake_run)
    out = ScalacTool().invoke({"paths": [rel]}, tool_ctx)
    assert out.startswith("scalac ok")
    # Confirm we ran scalac, not sbt
    assert captured["cmd"][0].endswith("/scalac")
    assert "compile" not in captured["cmd"]


def test_scalafmt_uses_repo_dir_when_config_present(
    tool_ctx: ToolContext, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scalafmt walks up from cwd looking for .scalafmt.conf. If we don't pin
    cwd to the cloned repo, it picks up nothing and fails with rc=16."""
    rel = _seed_scala(workspace)
    _seed_sbt_repo(workspace)

    monkeypatch.setattr(validate_mod.shutil, "which", lambda _name: "/bin/scalafmt")
    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate_mod.subprocess, "run", _fake_run)
    ScalafmtTool().invoke({"paths": [rel]}, tool_ctx)

    assert captured["cwd"] == str(workspace / "repos" / "pipelines-reports")


def test_trim_sbt_output_extracts_error_lines() -> None:
    text = "\n".join(
        [
            "[info] resolving deps",
            "[info] downloading 1 of 50",
        ]
        * 1000
        + [
            "[error] Foo.scala:3: oops",
            "[error] one error found",
        ]
    )
    trimmed = validate_mod._trim_sbt_output(text, max_chars=2000)
    assert "[error] Foo.scala:3: oops" in trimmed
    assert "downloading 1 of 50" not in trimmed


# ---------- convention_check ----------


def test_convention_check_clean_file(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "src/main/scala/Clean.scala"
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("class Clean { val x = 1 }\n")
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "ok" in out


def test_convention_check_no_println(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "Foo.scala"
    (workspace / rel).write_text(
        'class Foo {\n  println("hi")\n}\n'
    )
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "no_println" in out
    assert "Foo.scala:2:" in out


def test_convention_check_no_save_as_table(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "Foo.scala"
    (workspace / rel).write_text("df.saveAsTable(\"x\")\n")
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "no_save_as_table" in out


def test_convention_check_no_simple_date_format(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "Foo.scala"
    (workspace / rel).write_text("val f = new SimpleDateFormat(\"yyyy\")\n")
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "no_simple_date_format" in out


def test_convention_check_provider_must_call_finish_violation(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "FooRocketProvider.scala"
    (workspace / rel).write_text(
        'class FooRocketProvider extends RocketBuilder("foo") {\n'
        '  globalArg("lrdCode").add(new FooNode(_), "foo_workflow", List())\n'
        "}\n"
    )
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "provider_must_call_finish" in out


def test_convention_check_provider_passes_with_finish(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "FooRocketProvider.scala"
    (workspace / rel).write_text(
        'class FooRocketProvider extends RocketBuilder("foo") {\n'
        '  globalArg("lrdCode")\n'
        '    .add(new FooNode(_), "foo_workflow", List())\n'
        "    .finish()\n"
        "}\n"
    )
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "ok" in out


def test_convention_check_ddl_must_use_create_if_not_exists(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "ddl/foo.sql"
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CREATE TABLE foo (a string);\n")
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "ddl_must_use_create_table_if_not_exists" in out


def test_convention_check_ddl_passes_with_if_not_exists(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "ddl/foo.sql"
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CREATE TABLE IF NOT EXISTS foo (a string);\n")
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "ok" in out


def test_convention_check_skips_unrelated_suffix(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "notes.md"
    (workspace / rel).write_text("println is fine in markdown\n")
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "ok" in out


def test_convention_check_multiple_violations(
    tool_ctx: ToolContext, workspace: Path
) -> None:
    rel = "Foo.scala"
    (workspace / rel).write_text(
        'println("a")\n'
        'df.saveAsTable("b")\n'
    )
    out = ConventionCheckTool().invoke({"paths": [rel]}, tool_ctx)
    assert "no_println" in out
    assert "no_save_as_table" in out
    assert out.count("Foo.scala") >= 2


def test_convention_check_missing_file(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="path is not a file"):
        ConventionCheckTool().invoke({"paths": ["does_not_exist.scala"]}, tool_ctx)


def test_convention_check_requires_paths(tool_ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="non-empty 'paths' list"):
        ConventionCheckTool().invoke({}, tool_ctx)


def test_list_rules_exposed() -> None:
    rules = list_rules()
    assert "no_println" in rules
    assert "provider_must_call_finish" in rules
    assert len(rules) == 5
