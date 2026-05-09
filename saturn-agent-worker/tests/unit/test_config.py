from __future__ import annotations

import importlib

import pytest


def test_settings_loads_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure no real env leaks in.
    for key in list(__import__("os").environ):
        if key.startswith("SATURN_AGENT_"):
            monkeypatch.delenv(key)

    config = importlib.import_module("saturn_agent.config")
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.gemini_model == "gemini-2.5-pro"
    assert settings.max_tokens_per_job == 500_000
    assert settings.bitbucket_default_target_branch == "develop"


def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SATURN_AGENT_GEMINI_MODEL", "gemini-flash")
    monkeypatch.setenv("SATURN_AGENT_MAX_TOKENS_PER_JOB", "1000")

    config = importlib.import_module("saturn_agent.config")
    config.get_settings.cache_clear()
    settings = config.Settings()
    assert settings.gemini_model == "gemini-flash"
    assert settings.max_tokens_per_job == 1000
