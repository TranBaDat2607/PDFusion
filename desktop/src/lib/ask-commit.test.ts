import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createAskCommitter } from "./ask-commit";
import { IDLE_ASK, reduceAskEvent, startAsk, type AskState } from "./rag-ask";

const delta = (offset: number, text: string) => ({
  type: "answer_delta",
  data: { offset, text },
});
const fold = (event: ReturnType<typeof delta> | { type: string; data: unknown }) =>
  (s: AskState) => reduceAskEvent(s, event);
const isDelta = (event: { type: string }) => event.type === "answer_delta";

describe("createAskCommitter", () => {
  let commit: ReturnType<typeof vi.fn<(state: AskState) => void>>;

  beforeEach(() => {
    vi.useFakeTimers();
    commit = vi.fn();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function started() {
    const committer = createAskCommitter(commit, 50);
    committer.set(startAsk("doc"));
    commit.mockClear();
    return committer;
  }

  it("commits the first delta at once", () => {
    const committer = started();

    committer.fold(fold(delta(0, "Hel")), true);

    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit.mock.lastCall![0].draft).toBe("Hel");
  });

  it("commits deltas within the interval once, with all the text so far", () => {
    const committer = started();

    committer.fold(fold(delta(0, "Hel")), true);
    committer.fold(fold(delta(3, "lo ")), true);
    committer.fold(fold(delta(6, "there")), true);
    expect(commit).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(50);
    expect(commit).toHaveBeenCalledTimes(2);
    expect(commit.mock.lastCall![0].draft).toBe("Hello there");
  });

  // The terminal event may land while deltas are held back: the draft's last
  // piece must not be lost, and a timer must not later commit a state from
  // before the answer.
  it("commits a terminal event at once, and nothing stale after it", () => {
    const committer = started();
    committer.fold(fold(delta(0, "Hel")), true);
    committer.fold(fold(delta(3, "lo")), true);

    const answer = { type: "answer", data: { answer: "Hello" } };
    committer.fold(fold(answer), isDelta(answer));

    expect(commit).toHaveBeenCalledTimes(2);
    const last = commit.mock.lastCall![0];
    expect(last.status).toBe("done");
    expect(last.draft).toBe("");

    vi.advanceTimersByTime(500);
    expect(commit).toHaveBeenCalledTimes(2);
  });

  it("builds the next state on the held-back one, not on the last committed", () => {
    const committer = started();
    committer.fold(fold(delta(0, "Hel")), true);
    committer.fold(fold(delta(3, "lo")), true); // held back

    const retry = delta(0, "Hi"); // a retried request restarts at offset 0
    committer.fold(fold(retry), true);
    vi.advanceTimersByTime(50);

    expect(commit.mock.lastCall![0].draft).toBe("Hi");
  });

  it("drops what is held back on cancel", () => {
    const committer = started();
    committer.fold(fold(delta(0, "Hel")), true);
    committer.fold(fold(delta(3, "lo")), true);

    committer.cancel();
    vi.advanceTimersByTime(500);

    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("commits a new state at once and discards what the last question held back", () => {
    const committer = started();
    committer.fold(fold(delta(0, "old answer")), true);
    committer.fold(fold(delta(10, " goes on")), true); // held back

    committer.set(startAsk("doc"));
    vi.advanceTimersByTime(500);

    expect(commit).toHaveBeenCalledTimes(2);
    expect(commit.mock.lastCall![0]).toEqual(startAsk("doc"));
  });

  it("starts idle", () => {
    expect(createAskCommitter(commit, 50).state).toEqual(IDLE_ASK);
  });
});
