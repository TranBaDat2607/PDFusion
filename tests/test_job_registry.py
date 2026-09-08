"""The in-memory job registry behind every SSE endpoint (`api/jobs.py`).

Three things here are load-bearing and none of them were covered:

* `stream()` discards its job the moment the consumer detaches — the reason
  `/setup/engine` is polled rather than streamed.
* `_sweep_stale_locked` is the only thing that reclaims a job whose consumer
  never attached, and it must never reclaim one with a live worker, or the
  cancel path would be handed a job id the registry no longer knows.
* `serialize_sse_event` has to emit real JSON; sse-starlette would otherwise
  `str()` the payload into Python repr the frontend can't parse.

Async tests run through `asyncio.run` rather than pytest-asyncio, which the
project doesn't depend on.
"""

from __future__ import annotations

import asyncio
import enum
import json
from pathlib import Path

import pytest

from desktop_pdf_translator.api import jobs as jobs_module
from desktop_pdf_translator.api.jobs import Job, JobRegistry, serialize_sse_event


def run(coro):
    return asyncio.run(coro)


async def drain(registry: JobRegistry, job_id: str) -> list:
    return [event async for event in registry.stream(job_id)]


# ---------------------------------------------------------------------------
# create / get / discard
# ---------------------------------------------------------------------------


def test_a_created_job_is_retrievable_by_its_id():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        assert registry.get(job.job_id) is job

    run(scenario())


def test_job_ids_are_unique():
    async def scenario():
        registry = JobRegistry()
        ids = {(await registry.create()).job_id for _ in range(50)}
        assert len(ids) == 50

    run(scenario())


def test_an_unknown_id_is_none_rather_than_a_raise():
    """Route handlers branch on `is None` to answer 404."""
    assert JobRegistry().get("nope") is None


def test_discarding_an_unknown_id_is_a_no_op():
    run(JobRegistry().discard("nope"))


def test_streaming_an_unknown_id_yields_nothing():
    async def scenario():
        assert await drain(JobRegistry(), "nope") == []

    run(scenario())


# ---------------------------------------------------------------------------
# emit / finish / stream
# ---------------------------------------------------------------------------


def test_events_reach_the_stream_in_the_order_they_were_emitted():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.emit("progress", {"percent": 10})
        await job.emit("progress", {"percent": 40})
        await job.finish("done", {"ok": True})

        assert await drain(registry, job.job_id) == [
            {"type": "progress", "data": {"percent": 10}},
            {"type": "progress", "data": {"percent": 40}},
            {"type": "done", "data": {"ok": True}},
        ]

    run(scenario())


def test_the_terminal_sentinel_is_not_itself_yielded():
    """`_END` closes the iterator; it must never reach sse-starlette, which
    would try to serialize a bare `object()`."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.finish()
        assert await drain(registry, job.job_id) == [{"type": "done", "data": {}}]

    run(scenario())


def test_finish_marks_the_job_finished():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        assert job.finished is False
        await job.finish()
        assert job.finished is True

    run(scenario())


def test_an_error_is_terminal_the_same_way_done_is():
    """Without a terminal event the overlay waits forever, which is why every
    job path reports its failure through `finish("error", ...)`."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.finish("error", {"message": "boom"})
        assert await drain(registry, job.job_id) == [
            {"type": "error", "data": {"message": "boom"}}
        ]

    run(scenario())


def test_a_drained_stream_discards_its_job():
    """This is what makes a webview reload lose the handle — fine for a
    per-click translate, and the reason `/setup/engine` is polled instead."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.finish()
        await drain(registry, job.job_id)
        assert registry.get(job.job_id) is None

    run(scenario())


def test_a_detaching_consumer_discards_the_job_too():
    """Breaking out of the stream (the client went away) runs the same
    finally-discard as consuming it to completion."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.emit("progress", {"percent": 1})
        await job.finish()

        stream = registry.stream(job.job_id)
        assert await stream.__anext__() == {"type": "progress", "data": {"percent": 1}}
        await stream.aclose()

        assert registry.get(job.job_id) is None

    run(scenario())


def test_a_stream_waits_for_events_that_arrive_later():
    """The worker usually starts pushing after the SSE handler is already
    parked on `queue.get()`."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()

        async def worker():
            await asyncio.sleep(0)
            await job.emit("progress", {"percent": 50})
            await job.finish("done", {})

        task = asyncio.create_task(worker())
        events = await drain(registry, job.job_id)
        await task
        assert [e["type"] for e in events] == ["progress", "done"]

    run(scenario())


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


def test_cancel_sets_the_flag_and_reaches_the_processor():
    calls = []

    class _Processor:
        def cancel(self):
            calls.append("processor")

    job = Job(job_id="j", processor=_Processor())
    job.cancel()

    assert job.cancelled is True
    assert calls == ["processor"]


def test_a_job_without_a_processor_still_cancels():
    job = Job(job_id="j")
    job.cancel()
    assert job.cancelled is True


def test_a_raising_processor_cancel_does_not_skip_the_task_cancel():
    """The processor hook is best-effort; the asyncio task cancel below it is
    not. Letting the raise through would leave a wedged job running."""

    class _Processor:
        def cancel(self):
            raise RuntimeError("processor exploded")

    async def scenario():
        job = Job(job_id="j", processor=_Processor())
        job.task = asyncio.create_task(asyncio.sleep(30))
        await asyncio.sleep(0)

        job.cancel()

        assert job.cancelled is True
        with pytest.raises(asyncio.CancelledError):
            await job.task

    run(scenario())


def test_a_finished_task_is_not_cancelled_again():
    async def scenario():
        job = Job(job_id="j")
        job.task = asyncio.create_task(asyncio.sleep(0))
        await job.task
        job.cancel()
        assert job.task.cancelled() is False

    run(scenario())


def test_a_processor_without_a_cancel_method_is_tolerated():
    """`processor` is an opaque handle by design — jobs.py must not depend on
    the processor class, so it can't assume the attribute exists."""
    job = Job(job_id="j", processor=object())
    job.cancel()
    assert job.cancelled is True


# ---------------------------------------------------------------------------
# TTL sweep
# ---------------------------------------------------------------------------


def test_a_fresh_undrained_job_survives_the_sweep():
    async def scenario():
        registry = JobRegistry()
        recent = await registry.create()
        await registry.create()
        assert registry.get(recent.job_id) is recent

    run(scenario())


def test_an_undrained_job_past_the_ttl_is_reclaimed():
    """Nothing else frees a job whose SSE consumer never attached: `stream()`'s
    finally-discard only runs for a stream that was actually opened."""

    async def scenario():
        registry = JobRegistry()
        abandoned = await registry.create()
        abandoned.created_at -= jobs_module._JOB_TTL_SECONDS + 1

        await registry.create()  # the sweep runs on create

        assert registry.get(abandoned.job_id) is None

    run(scenario())


def test_a_job_with_a_live_worker_is_never_reclaimed():
    """Sweeping one would hand `/translate/{id}/cancel` an id the registry no
    longer knows, silently turning Cancel into a 404 on a job still running."""

    async def scenario():
        registry = JobRegistry()
        working = await registry.create()
        working.created_at -= jobs_module._JOB_TTL_SECONDS * 10
        working.task = asyncio.create_task(asyncio.sleep(30))
        await asyncio.sleep(0)

        await registry.create()
        assert registry.get(working.job_id) is working

        working.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await working.task

    run(scenario())


def test_an_old_job_whose_worker_has_finished_is_reclaimed():
    async def scenario():
        registry = JobRegistry()
        done = await registry.create()
        done.created_at -= jobs_module._JOB_TTL_SECONDS + 1
        done.task = asyncio.create_task(asyncio.sleep(0))
        await done.task

        await registry.create()
        assert registry.get(done.job_id) is None

    run(scenario())


def test_the_sweep_leaves_the_job_that_triggered_it_alone():
    async def scenario():
        registry = JobRegistry()
        for _ in range(3):
            job = await registry.create()
            job.created_at -= jobs_module._JOB_TTL_SECONDS + 1
        newest = await registry.create()
        assert registry.get(newest.job_id) is newest

    run(scenario())


# ---------------------------------------------------------------------------
# serialize_sse_event
# ---------------------------------------------------------------------------


def test_the_payload_is_serialized_as_json_not_python_repr():
    """sse-starlette 2.4 passes `data` through `str()`, which would emit
    single-quoted Python repr the JS side can't `JSON.parse`."""
    wire = serialize_sse_event({"type": "done", "data": {"ok": True, "n": 3}})
    assert wire["event"] == "done"
    assert json.loads(wire["data"]) == {"ok": True, "n": 3}
    assert "'" not in wire["data"]


def test_a_path_payload_survives_serialization():
    """`chunk_ready` and `done` carry filesystem paths, which json.dumps
    refuses without the `default=str` this relies on."""
    wire = serialize_sse_event(
        {"type": "chunk_ready", "data": {"path": Path("C:/tmp/a_translated_v1.pdf")}}
    )
    assert "a_translated_v1.pdf" in json.loads(wire["data"])["path"]


def test_an_enum_payload_is_stringified_rather_than_raising():
    class _Status(enum.Enum):
        DONE = "done"

    wire = serialize_sse_event({"type": "done", "data": {"status": _Status.DONE}})
    assert json.loads(wire["data"])["status"] == str(_Status.DONE)


def test_an_empty_payload_is_an_empty_json_object():
    assert json.loads(serialize_sse_event({"type": "done", "data": {}})["data"]) == {}


# ---------------------------------------------------------------------------
# singleton
# ---------------------------------------------------------------------------


def test_the_registry_is_a_process_wide_singleton(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(jobs_module, "_REGISTRY", None)
    assert jobs_module.get_registry() is jobs_module.get_registry()
