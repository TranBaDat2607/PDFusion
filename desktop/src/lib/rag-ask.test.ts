import { describe, expect, it } from "vitest";

import {
  IDLE_ASK,
  INDEX_UNAVAILABLE,
  NOT_INDEXED,
  answerForDocument,
  failAsk,
  needsReindex,
  reduceAskEvent,
  startAsk,
  streamEnded,
  type RagAnswer,
} from "./rag-ask";

/** A complete `AskResultPayload`; only the answer text varies per test. */
function makeAnswer(answer: string): RagAnswer {
  return {
    answer,
    elapsed_seconds: 1.0,
    processing_time: 1.0,
    sources_used: { pdf_sources: 1 },
    timestamp: "2026-09-09T00:00:00",
  };
}

describe("reduceAskEvent", () => {
  it("records each progress message as a finished step", () => {
    let state = startAsk("doc-b");
    state = reduceAskEvent(state, {
      type: "progress",
      data: { message: "Searching the document", progress: 30 },
    });
    state = reduceAskEvent(state, {
      type: "progress",
      data: { message: "Writing the answer", progress: 80 },
    });

    expect(state).toMatchObject({
      status: "asking",
      documentId: "doc-b",
      message: "Writing the answer",
      progress: 80,
    });
    expect(state.actions.map((a) => [a.id, a.description])).toEqual([
      [1, "Searching the document"],
      [2, "Writing the answer"],
    ]);
  });

  it("keeps the answer event's payload when done repeats it", () => {
    const answer = makeAnswer("From the answer event");
    let state = reduceAskEvent(startAsk("doc-b"), {
      type: "answer",
      data: answer,
    });
    state = reduceAskEvent(state, {
      type: "done",
      data: makeAnswer("From the done event"),
    });

    expect(state.status).toBe("done");
    expect(state.answer).toBe(answer);
  });

  it("takes the answer from done when no answer event came first", () => {
    const state = reduceAskEvent(startAsk("doc-b"), {
      type: "done",
      data: makeAnswer("From the done event"),
    });

    expect(state).toMatchObject({ status: "done", progress: 100 });
    expect(state.answer?.answer).toBe("From the done event");
  });

  it("turns an error event into an error for the same document", () => {
    const state = reduceAskEvent(startAsk("doc-b"), {
      type: "error",
      data: { message: "This document isn't indexed yet." },
    });

    expect(state).toMatchObject({
      status: "error",
      documentId: "doc-b",
      error: "This document isn't indexed yet.",
    });
    expect(state).not.toHaveProperty("errorCode");
  });

  it("keeps the code of an error the panel acts on", () => {
    const state = reduceAskEvent(startAsk("doc-b"), {
      type: "error",
      data: { message: "The index was cleared.", code: INDEX_UNAVAILABLE },
    });

    expect(state).toMatchObject({
      status: "error",
      error: "The index was cleared.",
      errorCode: INDEX_UNAVAILABLE,
    });
  });

  it("ignores events it doesn't know", () => {
    const state = startAsk("doc-b");
    expect(reduceAskEvent(state, { type: "ping", data: {} })).toBe(state);
  });
});

describe("streamEnded", () => {
  it("fails a question still waiting for its answer", () => {
    expect(streamEnded(startAsk("doc-b"))).toMatchObject({
      status: "error",
      error: "Answer stream ended unexpectedly",
    });
  });

  it("leaves a finished question alone", () => {
    const done = reduceAskEvent(startAsk("doc-b"), {
      type: "answer",
      data: makeAnswer("Done"),
    });
    expect(streamEnded(done)).toBe(done);
  });
});

describe("answerForDocument", () => {
  const answeredAboutA = reduceAskEvent(startAsk("doc-a"), {
    type: "answer",
    data: makeAnswer("About A"),
  });

  it("hands the answer to the document it was asked about", () => {
    expect(answerForDocument(answeredAboutA, "doc-a")?.answer).toBe("About A");
  });

  // The bug (#59): open A, ask, switch to B before the answer arrives — and
  // A's answer was appended to B's chat.
  it("withholds it once a different document is open", () => {
    expect(answerForDocument(answeredAboutA, "doc-b")).toBeNull();
    expect(answerForDocument(answeredAboutA, null)).toBeNull();
  });

  it("has nothing to hand over until an answer arrives", () => {
    expect(answerForDocument(IDLE_ASK, "doc-a")).toBeNull();
    expect(answerForDocument(startAsk("doc-a"), "doc-a")).toBeNull();
    expect(
      answerForDocument(failAsk(startAsk("doc-a"), "boom"), "doc-a"),
    ).toBeNull();
  });
});

describe("needsReindex", () => {
  it("indexes the open document again when it has no usable index", () => {
    expect(
      needsReindex(failAsk(startAsk("doc-a"), "409", NOT_INDEXED), "doc-a"),
    ).toBe(true);
    expect(
      needsReindex(
        failAsk(startAsk("doc-a"), "cleared", INDEX_UNAVAILABLE),
        "doc-a",
      ),
    ).toBe(true);
  });

  it("leaves every other failure, and every other state, alone", () => {
    expect(
      needsReindex(
        failAsk(startAsk("doc-a"), "OpenAI rejected the API key"),
        "doc-a",
      ),
    ).toBe(false);
    expect(needsReindex(startAsk("doc-a"), "doc-a")).toBe(false);
    expect(needsReindex(IDLE_ASK, "doc-a")).toBe(false);
  });

  // The question was about A and B is open now: indexing B fixes nothing.
  it("only for the document the question was about", () => {
    const failed = failAsk(startAsk("doc-a"), "cleared", INDEX_UNAVAILABLE);
    expect(needsReindex(failed, "doc-b")).toBe(false);
    expect(needsReindex(failed, null)).toBe(false);
  });
});
