"""In-memory job registry for streaming long-running async work over SSE.

Each job has an asyncio.Queue. The worker pushes events; the SSE handler drains
them. A terminal event (type=`done` or `error`) closes the queue.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

# Sentinel marking the terminal event. SSE handler stops iterating after this.
_END = object()


@dataclass
class Job:
    job_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancelled: bool = False
    task: Optional[asyncio.Task] = None
    finished: bool = False

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
            job_id = uuid.uuid4().hex
            job = Job(job_id=job_id)
            self._jobs[job_id] = job
            return job

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
