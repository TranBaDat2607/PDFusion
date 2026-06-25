"""In-memory job registry for streaming long-running async work over SSE.

Each job has an asyncio.Queue. The worker pushes events; the SSE handler drains
them. A terminal event (type=`done` or `error`) closes the queue.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

# Sentinel marking the terminal event. SSE handler stops iterating after this.
_END = object()

# Jobs whose worker has finished (or never had one) but were never drained by
# an SSE consumer are reclaimed once they exceed this age. Generous enough that
# a slow client reconnecting still finds its job.
_JOB_TTL_SECONDS = 3600.0


@dataclass
class Job:
    job_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancelled: bool = False
    task: Optional[asyncio.Task] = None
    finished: bool = False
    created_at: float = field(default_factory=time.monotonic)
    # Opaque handle so the API layer can call `processor.reprioritize(...)`
    # for the priority-scheduler endpoint without taking a hard dep on the
    # processor class here. None when the job isn't using a processor.
    processor: Optional[Any] = None

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        await self.queue.put({"type": event_type, "data": payload})

    async def finish(self, event_type: str = "done", payload: Optional[Dict[str, Any]] = None) -> None:
        await self.queue.put({"type": event_type, "data": payload or {}})
        await self.queue.put(_END)
        self.finished = True

    def cancel(self) -> None:
        self.cancelled = True
        if self.task and not self.task.done():
            self.task.cancel()


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> Job:
        async with self._lock:
            self._sweep_stale_locked()
            job_id = uuid.uuid4().hex
            job = Job(job_id=job_id)
            self._jobs[job_id] = job
            return job

    def _sweep_stale_locked(self) -> None:
        """Reclaim jobs that were never drained by an SSE consumer (so
        `stream()`'s finally-discard never ran) and have outlived the TTL.

        Only removes jobs whose worker task is absent or already done — a job
        with an in-flight worker is left alone so the cancel path stays valid.
        Caller must hold `self._lock`.
        """
        now = time.monotonic()
        stale = [
            jid
            for jid, job in self._jobs.items()
            if (job.task is None or job.task.done())
            and (now - job.created_at) > _JOB_TTL_SECONDS
        ]
        for jid in stale:
            self._jobs.pop(jid, None)
        if stale:
            logger.info("Reclaimed %d stale job(s) from the registry", len(stale))

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def discard(self, job_id: str) -> None:
        async with self._lock:
            self._jobs.pop(job_id, None)

    async def stream(self, job_id: str) -> AsyncIterator[Dict[str, Any]]:
        """Yield events for an SSE response until the terminal sentinel arrives."""
        job = self.get(job_id)
        if job is None:
            return
        try:
            while True:
                item = await job.queue.get()
                if item is _END:
                    break
                yield item
        finally:
            # Free memory once the stream is consumed
            await self.discard(job_id)


_REGISTRY: Optional[JobRegistry] = None


def get_registry() -> JobRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = JobRegistry()
    return _REGISTRY


def serialize_sse_event(event: Dict[str, Any]) -> Dict[str, str]:
    """Convert a queued job event into the dict shape sse-starlette expects.

    sse-starlette 2.4 passes `data` through `str()` instead of `json.dumps`,
    which would emit Python repr (single quotes, `<EventType.X: 'x'>`) that
    the JS side can't `JSON.parse`. Serialize ourselves so the wire format
    is real JSON. `default=str` covers Path objects and other non-JSON types.
    """
    return {
        "event": event["type"],
        "data": json.dumps(event["data"], default=str),
    }
