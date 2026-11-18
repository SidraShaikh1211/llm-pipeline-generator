from __future__ import annotations

import uuid
from typing import Any, Protocol

from agent_worker.observability.logging import get_logger
from agent_worker.persistence.repository import JobRepository


_log = get_logger(__name__)


_DEFAULT_MAX_VALUE_LEN = 80


class EventRecorder(Protocol):
    """Sink for fine-grained agent events (llm.called, tool.invoked, ...)."""

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None: ...


class NoOpEventRecorder:
    """Default recorder for standalone tests and library use; drops everything."""

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        return None


class JobEventRecorder:
    """Writes events to the JobRepository and emits a structured log line.

    Bound to a single (job_id, stage) pair; build a fresh recorder per stage.
    contextvars (job_id, user_id, stage) bound by the orchestrator are inherited
    by structlog automatically, so payloads here only need to carry event-specific
    fields.
    """

    def __init__(
        self,
        repository: JobRepository,
        job_id: uuid.UUID,
        stage: str,
    ) -> None:
        self.repository = repository
        self.job_id = job_id
        self.stage = stage

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        body = dict(payload or {})
        self.repository.append_event(
            job_id=self.job_id,
            stage=self.stage,
            event_type=event_type,
            payload=body,
        )
        _log.info(event_type, **body)


def summarize_args(
    arguments: dict[str, Any], *, max_value_len: int = _DEFAULT_MAX_VALUE_LEN
) -> dict[str, Any]:
    """Compact a tool-call arguments dict for journal/log payloads.

    Long string values (e.g., generated file content passed to write_file) are
    truncated and replaced with `<head>...(N chars)`. Non-string values pass
    through; container values are summarized by length.
    """
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            if len(value) > max_value_len:
                out[key] = f"{value[:max_value_len]}...({len(value)} chars)"
            else:
                out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = f"<{type(value).__name__} len={len(value)}>"
        elif isinstance(value, dict):
            out[key] = f"<dict keys={len(value)}>"
        else:
            out[key] = value
    return out
