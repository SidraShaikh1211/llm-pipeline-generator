from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# Repo root .env (pipeline_automation/.env) is the shared file. cwd .env (if any)
# takes precedence — later entries in the tuple win in pydantic-settings.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SATURN_AGENT_",
        env_file=(str(_REPO_ROOT_ENV), ".env"),
        extra="ignore",
    )

    # LLM — Gemini
    gemini_api_url: str = "http://localhost:0/unset"
    gemini_api_key: SecretStr = SecretStr("unset")
    gemini_model: str = "gemini-2.5-pro"
    gemini_temperature: float = 0.1
    gemini_max_output_tokens: int = 8192

    # LLM — Claude (Anthropic)
    claude_api_key: SecretStr | None = None
    claude_model: str = "claude-opus-4-7"
    claude_effort: str = "high"  # low | medium | high | xhigh (Opus 4.7 only) | max (Opus tier)
    claude_max_tokens: int = 16000

    # Workspace
    workspace_root: str = "/workspace"
    workspace_ttl_days: int = 7

    # Token budget
    max_tokens_per_job: int = 500_000

    # Persistence
    postgres_dsn: str = "postgresql+psycopg://saturn:saturn@localhost:5432/saturn_agent"
    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: SecretStr = SecretStr("unset")
    minio_secret_key: SecretStr = SecretStr("unset")
    minio_bucket: str = "saturn-agent"

    # Bitbucket
    bitbucket_base_url: str = "https://bitbucket.example.com"
    bitbucket_service_account_token: SecretStr = SecretStr("unset")
    bitbucket_default_target_branch: str = "develop"

    # Observability
    otlp_endpoint: str | None = None
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
