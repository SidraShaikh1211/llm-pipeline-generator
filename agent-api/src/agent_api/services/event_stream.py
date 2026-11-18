from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from agent_worker.orchestrator import TERMINAL_STAGES
from agent_worker.orchestrator.state_machine import Stage
from agent_worker.persistence import JobRepository


_TERMINAL_VALUES = {s.value for s in TERMINAL_STAGES}


async def event_stream(
    repository: JobRepository,
    job_id: uuid.UUID,
    *,
    poll_interval_s: float = 0.5,
    idle_timeout_s: float = 300.0,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events for a job by polling its event tape.

    Yields each new event the moment it appears, then closes once the job
    reaches a terminal stage. Idle for >idle_timeout_s without progress also
    closes the stream.
    """
    seen = 0
    elapsed = 0.0
    while True:
        events = repository.list_events(job_id)
        new = events[seen:]
        for ev in new:
            yield {
                "event": ev.event_type,
                "data": json.dumps(ev.to_json_safe()),
            }
        if new:
            seen = len(events)
            elapsed = 0.0
        else:
            elapsed += poll_interval_s

        record = repository.get_job(job_id)
        if record is not None and record.stage in _TERMINAL_VALUES:
            return
        if elapsed >= idle_timeout_s:
            yield {
                "event": "stream.timeout",
                "data": json.dumps({"job_id": str(job_id), "idle_s": elapsed}),
            }
            return

        await asyncio.sleep(poll_interval_s)
