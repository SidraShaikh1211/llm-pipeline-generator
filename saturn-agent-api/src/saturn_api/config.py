from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_WORKSPACE = Path("/tmp/saturn-api-workspaces")
# pipeline_automation/.env — shared with the worker. cwd .env wins if both exist.
_SHARED_ENV = _REPO_ROOT.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SATURN_API_",
        env_file=(str(_SHARED_ENV), ".env"),
        extra="ignore",
    )

    workspace_root: Path = _DEFAULT_WORKSPACE

    skills_root: Path | None = None
    """Path to the worker's `skills/` folder. If unset, the API will try to
    locate the worker package and use its bundled skills."""

    max_tokens_per_job: int = 500_000

    default_mock_script_path: Path | None = None
    """If set, jobs that don't supply mock_script_path use this instead. Useful
    for demos and dev environments without real Gemini credentials."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    log_level: str = "INFO"

    max_concurrent_jobs: int = 4

    llm_provider: str = "claude"
    """Which LLM to route to when a job request omits mock_script_path.
    Values: 'claude' (Anthropic), 'gemini' (Google), or 'mock' (require mock_script_path on every request)."""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
