from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from saturn_agent.workspace import repo_clone as rc_mod
from saturn_agent.workspace.repo_clone import RepoCloneError, RepoCloneService


def test_clone_remote_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rc_mod.shutil, "which", lambda _name: "/usr/bin/git")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rc_mod.subprocess, "run", _fake_run)
    target = tmp_path / "out"
    RepoCloneService().clone("https://github.com/org/repo", target)
    assert captured["cmd"][:3] == ["git", "clone", "--quiet"]
    assert captured["cmd"][-2:] == ["https://github.com/org/repo", str(target)]
    assert target.parent.exists()


def test_clone_local_path_must_exist(tmp_path: Path) -> None:
    with pytest.raises(RepoCloneError, match="does not exist"):
        RepoCloneService().clone(str(tmp_path / "nope"), tmp_path / "out")


def test_clone_local_path_must_be_git(tmp_path: Path) -> None:
    src = tmp_path / "plain"
    src.mkdir()
    (src / "file.txt").write_text("x")
    with pytest.raises(RepoCloneError, match="not a git repo"):
        RepoCloneService().clone(str(src), tmp_path / "out")


def test_clone_local_path_passed_to_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "real-repo"
    (src / ".git").mkdir(parents=True)
    monkeypatch.setattr(rc_mod.shutil, "which", lambda _name: "/usr/bin/git")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, **_k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rc_mod.subprocess, "run", _fake_run)
    RepoCloneService().clone(str(src), tmp_path / "out")
    # Local path resolved to absolute and passed to git
    assert captured["cmd"][-2] == str(src.resolve())


def test_clone_branch_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rc_mod.shutil, "which", lambda _name: "/usr/bin/git")
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd, **_k):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rc_mod.subprocess, "run", _fake_run)
    RepoCloneService().clone(
        "https://github.com/org/repo", tmp_path / "out", branch="develop"
    )
    assert "--branch" in captured["cmd"]
    assert "develop" in captured["cmd"]


def test_clone_failure_surfaces_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rc_mod.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        rc_mod.subprocess,
        "run",
        lambda cmd, **_k: subprocess.CompletedProcess(
            cmd, 128, "", "fatal: repository not found"
        ),
    )
    with pytest.raises(RepoCloneError, match="repository not found"):
        RepoCloneService().clone("https://x.example/missing", tmp_path / "out")


def test_clone_target_must_be_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rc_mod.shutil, "which", lambda _name: "/usr/bin/git")
    target = tmp_path / "out"
    target.mkdir()
    (target / "leftover").write_text("x")
    with pytest.raises(RepoCloneError, match="not empty"):
        RepoCloneService().clone("https://github.com/org/repo", target)


def test_clone_skips_when_git_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rc_mod.shutil, "which", lambda _name: None)
    with pytest.raises(RepoCloneError, match="git not installed"):
        RepoCloneService().clone("https://github.com/org/repo", tmp_path / "out")


def test_clone_timeout_surfaces_clean_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rc_mod.shutil, "which", lambda _name: "/usr/bin/git")

    def _raises(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(rc_mod.subprocess, "run", _raises)
    with pytest.raises(RepoCloneError, match="timed out"):
        RepoCloneService(timeout_s=1).clone(
            "https://github.com/org/repo", tmp_path / "out"
        )


def test_clone_empty_source_raises(tmp_path: Path) -> None:
    with pytest.raises(RepoCloneError, match="non-empty"):
        RepoCloneService().clone("", tmp_path / "out")
