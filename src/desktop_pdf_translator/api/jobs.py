"""In-memory job registry for streaming long-running async work over SSE.

Each job keeps an append-only `history` of every event it has emitted, plus
an `asyncio.Condition` that wakes anyone waiting on it. A `stream()` call
replays whatever in `history` is newer than the `last_seq` it was given, then
tails live events the same way — so job state lives entirely in the shared
`Job`, never in a per-consumer queue, and a reattaching or concurrent
`stream()` call needs no attach/detach bookkeeping of its own.

Nothing here discards a job on disconnect. A dropped SSE connection (a
webview reload, a network blip) leaves the job running and still reachable
by `/cancel` and future `stream()` calls; only `_sweep_stale_locked` frees a
job, and only once it has been finished (or never had a worker) for
`_JOB_TTL_SECONDS`. A terminal event (`done`/`error`/`cancelled`) is always
the last item `history` will ever gain — `stream()` stops after yielding it.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

# Jobs are reclaimed once they've been idle (no live worker) for this long,
# measured from `finished_at` when the job has one, else `created_at` — a job
# that ran for ten minutes still gets a full post-completion grace window,
# not one eroded by its own runtime. Generous enough that a slow client
# reconnecting still finds its job.
_JOB_TTL_SECONDS = 3600.0


@dataclass
class Job:
    job_id: str
    cancelled: bool = False
    task: Optional[asyncio.Task] = None
    finished: bool = False
    finished_at: Optional[float] = None
    created_at: float = field(default_factory=time.monotonic)
    # Opaque handle so the API layer can call `processor.reprioritize(...)`
    # for the priority-scheduler endpoint without taking a hard dep on the
    # processor class here. None when the job isn't using a processor, and
    # cleared once the job finishes — see `finish()`.
    processor: Optional[Any] = None
    # Every event this job has ever emitted, in order. `seq` is just the
    # 1-based position — no separate counter needed. Small (dozens to a few
    # thousand small dicts even for a chatty long translate), and is what
    # lets a reattaching client ask for "everything after N".
    history: List[Dict[str, Any]] = field(default_factory=list)
    # Constructing this outside a running event loop is fine — like
    # `asyncio.Queue()` before it, it binds to the loop lazily on first
    # `acquire`/`wait`, which is what lets tests build a bare `Job(...)`
    # synchronously.
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        async with self.condition:
            self.history.append(
                {"seq": len(self.history) + 1, "type": event_type, "data": payload}
            )
            self.condition.notify_all()

    async def finish(self, event_type: str = "done", payload: Optional[Dict[str, Any]] = None) -> None:
        async with self.condition:
            self.history.append(
                {"seq": len(self.history) + 1, "type": event_type, "data": payload or {}}
            )
            self.finished = True
            self.finished_at = time.monotonic()
            self.condition.notify_all()
        # A finished job now lingers in the registry for reattachment instead
        # of self-discarding, so holding a whole PDFProcessor (BabelDOC
        # state) for the TTL window would be a real memory regression. Both
        # readers of `.processor` already tolerate None: `/reprioritize`
        # checks `job.finished` first, and `cancel()`'s getattr is a no-op.
        self.processor = None

    def cancel(self) -> None:
        self.cancelled = True
        # Opaque, like `processor.reprioritize(...)` above — stops in-flight
        # translate() calls without jobs.py depending on PDFProcessor.
        # Best-effort: a raise here must not skip the task cancel below.
        try:
            processor_cancel = getattr(self.processor, "cancel", None)
            if callable(processor_cancel):
                processor_cancel()
        except Exception:
            logger.warning("Job %s: processor.cancel() failed", self.job_id, exc_info=True)
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
        """Reclaim jobs that have had no live worker, and no fresh terminal
        event, for `_JOB_TTL_SECONDS`.

        This is the *only* discard path now — `stream()` never discards on
        drain or on detach, so every finished job (whether or not anything
        ever consumed its events) relies on this to eventually free memory.
        Only removes jobs whose worker task is absent or already done — a
        job with an in-flight worker is left alone so the cancel path stays
        valid. Caller must hold `self._lock`.
        """
        now = time.monotonic()
        stale = [
            jid
            for jid, job in self._jobs.items()
            if (job.task is None or job.task.done())
            and (now - (job.finished_at if job.finished_at is not None else job.created_at))
            > _JOB_TTL_SECONDS
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

    async def stream(self, job_id: str, last_seq: int = 0) -> AsyncIterator[Dict[str, Any]]:
        """Yield events for an SSE response, replaying anything after
        `last_seq` before tailing live ones, until the terminal event.

        Never discards the job — see the module docstring. The lock is held
        only to snapshot state, never across a `yield`: `EventSourceResponse`
        pulls the next item only after writing the current one to the
        socket, so a generator parked at `yield` while holding `condition`
        would block the *worker's* `emit()` behind a slow client.
        """
        job = self.get(job_id)
        if job is None:
            return
        pos = last_seq
        while True:
            async with job.condition:
                await job.condition.wait_for(
                    lambda: len(job.history) > pos or job.finished
                )
                # `history[pos:]` on a `last_seq` past the current length is
                # `[]`, not an error — a stale/bogus id degrades to "nothing
                # new" rather than raising.
                pending = job.history[pos:]
                pos = len(job.history)
                done = job.finished
            for item in pending:
                yield item
            if done:
                return


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

    `seq`, when present, becomes the SSE `id:` line — real wire framing (see
    `sse_starlette.sse.ServerSentEvent`), not part of the JSON payload, which
    is what a reattaching client sends back as `last_seq`.
    """
    wire: Dict[str, str] = {
        "event": event["type"],
        "data": json.dumps(event["data"], default=str),
    }
    if "seq" in event:
        wire["id"] = str(event["seq"])
    return wire
