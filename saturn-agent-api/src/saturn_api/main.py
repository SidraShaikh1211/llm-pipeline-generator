from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from saturn_agent.observability.logging import configure_logging
from saturn_agent.persistence import InMemoryJobRepository, JobRepository

from saturn_api.config import Settings, get_settings
from saturn_api.routes import health_router, jobs_router


def _load_dotenv_into_environ() -> None:
    """Load .env into os.environ before Settings instances are constructed.

    pydantic-settings reads .env into Settings fields but does NOT export to
    os.environ — which means third-party SDKs that read their keys from
    os.environ (Anthropic SDK reads ANTHROPIC_API_KEY, Google SDK reads
    GEMINI_API_KEY, etc.) won't see anything in .env. This step bridges the gap.

    Looks at: cwd/.env first, then the repo root .env. Existing env vars take
    precedence (we don't override anything already set on the command line).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # python-dotenv missing — nothing to do, Settings still works

    candidates = [Path.cwd() / ".env"]
    # repo root: pipeline_automation/saturn-agent-api/src/saturn_api/main.py → parents[3]
    candidates.append(Path(__file__).resolve().parents[3] / ".env")
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)


_load_dotenv_into_environ()


def create_app(
    *,
    settings: Settings | None = None,
    repository: JobRepository | None = None,
) -> FastAPI:
    cfg = settings or get_settings()
    repo = repository or InMemoryJobRepository()
    executor = ThreadPoolExecutor(max_workers=cfg.max_concurrent_jobs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(level=cfg.log_level, json_output=True)
        cfg.workspace_root.mkdir(parents=True, exist_ok=True)
        try:
            yield
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="Saturn Agent API",
        version="0.0.1",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.repository = repo
    app.state.executor = executor

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(jobs_router)
    return app


# Module-level app for `uvicorn saturn_api.main:app`
app = create_app()


def run() -> None:
    """Console-script entry point: `saturn-api`."""
    import uvicorn

    uvicorn.run(
        "saturn_api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    run()
