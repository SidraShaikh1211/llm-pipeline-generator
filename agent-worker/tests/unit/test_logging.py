from __future__ import annotations

import io
import json
import logging

import pytest
import structlog

from agent_worker.observability.logging import (
    bind_job_context,
    bind_stage,
    configure_logging,
    get_logger,
    reset_logging,
    unbind_stage,
)


@pytest.fixture(autouse=True)
def _reset_after() -> None:
    yield
    reset_logging()


def test_configure_logging_emits_json_with_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_output=True)
    bind_job_context(job_id="job-123", user_id="alice")
    bind_stage("planning")
    log = get_logger("test")
    log.info("hello", foo=1)
    captured = capsys.readouterr().err.strip()
    assert captured, "expected at least one log line on stderr"
    # The last line should be parseable JSON.
    last = captured.splitlines()[-1]
    payload = json.loads(last)
    assert payload["event"] == "hello"
    assert payload["job_id"] == "job-123"
    assert payload["user_id"] == "alice"
    assert payload["stage"] == "planning"
    assert payload["foo"] == 1
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_unbind_stage_removes_stage(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", json_output=True)
    bind_job_context(job_id="job-x")
    bind_stage("planning")
    unbind_stage()
    log = get_logger("test")
    log.info("after_unbind")
    last = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(last)
    assert "stage" not in payload


def test_console_renderer_is_human_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import re

    configure_logging(level="INFO", json_output=False)
    log = get_logger("test")
    log.info("hi", x=1)
    output = capsys.readouterr().err
    # Strip ANSI escape codes so the assertion is renderer-version-stable.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    assert "hi" in plain
    assert "x=1" in plain


def test_log_level_filters(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="WARNING", json_output=True)
    log = get_logger("test")
    log.info("should_be_dropped")
    log.warning("should_appear")
    err = capsys.readouterr().err.strip()
    assert "should_be_dropped" not in err
    assert "should_appear" in err
