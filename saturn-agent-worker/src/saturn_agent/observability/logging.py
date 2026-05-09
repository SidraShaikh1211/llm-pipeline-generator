from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, unbind_contextvars


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog and stdlib logging for the worker.

    Idempotent — safe to call multiple times. The CLI calls it on entry; tests
    can opt in with reset_logging() between cases.
    """
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        stream=sys.stderr,
        force=True,
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def reset_logging() -> None:
    """For tests: drop structlog config so the next configure_logging starts clean."""
    structlog.reset_defaults()
    clear_contextvars()


def bind_job_context(*, job_id: str, user_id: str | None = None) -> None:
    """Bind a job's identity into contextvars so every subsequent log line carries it."""
    bind_contextvars(job_id=job_id, **({"user_id": user_id} if user_id else {}))


def bind_stage(stage: str) -> None:
    bind_contextvars(stage=stage)


def unbind_stage() -> None:
    unbind_contextvars("stage")


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()
