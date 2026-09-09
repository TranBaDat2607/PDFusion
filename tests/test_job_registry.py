"""The in-memory job registry behind every SSE endpoint (`api/jobs.py`).

Three things here are load-bearing and none of them were covered before
issue #28:

* `stream()` never discards its job — not on drain, not on a consumer
  detaching early. The only discard path is `_sweep_stale_locked`, which
  reclaims a job once it has had no live worker and no fresh terminal event
  for `_JOB_TTL_SECONDS`.
* `stream(job_id, last_seq=N)` replays whatever in `history` is newer than
  `N` before tailing live events — what lets a reconnecting client pick up
  where it left off instead of losing everything since the drop.
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


async def drain(registry: JobRegistry, job_id: str, last_seq: int = 0) -> list:
    """Stripped of `seq` — most tests only care about type/data ordering.
    Tests that care about `seq` itself read `registry.stream(...)` directly."""
    return [
        {"type": e["type"], "data": e["data"]}
        async for e in registry.stream(job_id, last_seq=last_seq)
    ]


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


def test_a_finish_only_job_yields_exactly_one_terminal_event():
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


def test_finish_records_when_the_job_finished():
    async def scenario():
        job = Job(job_id="j")
        assert job.finished_at is None
        await job.finish()
        assert job.finished_at is not None

    run(scenario())


def test_finish_clears_the_processor_handle():
    """A finished job now lingers in the registry for reattachment instead of
    self-discarding; holding a whole PDFProcessor for that window would be a
    real memory regression. Both readers of `.processor` already tolerate
    None (see `/reprioritize` and `cancel()`)."""

    async def scenario():
        job = Job(job_id="j", processor=object())
        await job.finish()
        assert job.processor is None

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


def test_a_drained_stream_keeps_its_job_reattachable():
    """A full drain used to discard the job (fine when nothing could ever
    reattach). Now it stays — a later reattach with `last_seq` at the end
    should see nothing new and close cleanly, which only works if the job is
    still there to find."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.finish()
        await drain(registry, job.job_id)
        assert registry.get(job.job_id) is job

    run(scenario())


def test_a_detaching_consumer_leaves_the_job_intact_for_reattach():
    """Breaking out of the stream (the client went away) must NOT discard the
    job — that was issue #28: a still-running job became uncancellable
    (`/cancel` -> 404) the moment its SSE consumer merely detached."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.emit("progress", {"percent": 1})
        await job.finish()

        stream = registry.stream(job.job_id)
        assert await stream.__anext__() == {
            "seq": 1,
            "type": "progress",
            "data": {"percent": 1},
        }
        await stream.aclose()

        assert registry.get(job.job_id) is job

    run(scenario())


def test_reattaching_after_detach_no_longer_404s_at_the_registry():
    """The regression test for the actual reported bug: detach mid-stream,
    then confirm the job is still there for `/cancel` (which just does
    `registry.get(job_id)` then `job.cancel()`) to find."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.emit("progress", {"percent": 1})

        stream = registry.stream(job.job_id)
        await stream.__anext__()
        await stream.aclose()

        found = registry.get(job.job_id)
        assert found is not None
        found.cancel()
        assert found.cancelled is True

    run(scenario())


def test_a_stream_waits_for_events_that_arrive_later():
    """The worker usually starts pushing after the SSE handler is already
    parked on the condition."""

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
# replay by last_seq
# ---------------------------------------------------------------------------


def test_replay_from_last_seq_returns_only_newer_events():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.emit("progress", {"percent": 10})
        await job.emit("progress", {"percent": 40})
        await job.finish("done", {"ok": True})

        events = [e async for e in registry.stream(job.job_id, last_seq=2)]
        assert events == [{"seq": 3, "type": "done", "data": {"ok": True}}]

    run(scenario())


def test_reattaching_fully_caught_up_yields_nothing_and_closes():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.emit("progress", {"percent": 10})
        await job.finish()

        events = await asyncio.wait_for(
            drain(registry, job.job_id, last_seq=2), timeout=1
        )
        assert events == []

    run(scenario())


def test_last_seq_beyond_history_does_not_raise():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.finish()

        events = await asyncio.wait_for(
            drain(registry, job.job_id, last_seq=999), timeout=1
        )
        assert events == []

    run(scenario())


def test_history_seq_numbers_are_1_based_and_monotonic():
    async def scenario():
        job = Job(job_id="j")
        await job.emit("progress", {"percent": 1})
        await job.emit("progress", {"percent": 2})
        await job.finish()
        assert [e["seq"] for e in job.history] == [1, 2, 3]

    run(scenario())


def test_concurrent_streams_on_one_job_both_see_everything():
    """Two independent SSE consumers attaching to the same job (e.g. a
    reattach racing a not-yet-closed old connection) must each see the full
    sequence — state lives in the shared `Job`, not a per-consumer queue."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()

        async def worker():
            await asyncio.sleep(0)
            await job.emit("progress", {"percent": 30})
            await job.emit("progress", {"percent": 70})
            await job.finish("done", {})

        task = asyncio.create_task(worker())
        a, b = await asyncio.gather(
            drain(registry, job.job_id), drain(registry, job.job_id)
        )
        await task
        expected = [
            {"type": "progress", "data": {"percent": 30}},
            {"type": "progress", "data": {"percent": 70}},
            {"type": "done", "data": {}},
        ]
        assert a == expected
        assert b == expected

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
    """Nothing else frees a job whose SSE consumer never attached: `stream()`
    never discards on its own."""

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


def test_a_finished_undrained_job_survives_immediately_and_only_ttl_reclaims_it():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.finish()

        # Nothing discards it just for finishing.
        assert registry.get(job.job_id) is job

        job.finished_at -= jobs_module._JOB_TTL_SECONDS + 1
        await registry.create()  # the sweep runs on create
        assert registry.get(job.job_id) is None

    run(scenario())


def test_the_ttl_clock_for_a_finished_job_starts_at_finish_not_creation():
    """A job that ran for a long time before finishing must not have its
    post-completion grace window eroded by its own runtime."""

    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        job.created_at -= jobs_module._JOB_TTL_SECONDS * 10
        await job.finish()  # finished_at is "now" regardless of created_at

        await registry.create()  # the sweep runs on create
        assert registry.get(job.job_id) is job

    run(scenario())


def test_a_job_finished_long_ago_is_reclaimed_from_its_finish_time():
    async def scenario():
        registry = JobRegistry()
        job = await registry.create()
        await job.finish()
        job.finished_at -= jobs_module._JOB_TTL_SECONDS + 1

        await registry.create()  # the sweep runs on create
        assert registry.get(job.job_id) is None

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


def test_a_seq_in_the_event_becomes_the_sse_id_field():
    wire = serialize_sse_event({"type": "progress", "data": {}, "seq": 7})
    assert wire["id"] == "7"


def test_an_event_without_seq_omits_the_id_field():
    wire = serialize_sse_event({"type": "done", "data": {}})
    assert "id" not in wire


# ---------------------------------------------------------------------------
# singleton
# ---------------------------------------------------------------------------


def test_the_registry_is_a_process_wide_singleton(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(jobs_module, "_REGISTRY", None)
    assert jobs_module.get_registry() is jobs_module.get_registry()
