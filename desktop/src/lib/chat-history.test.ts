import { describe, expect, it } from "vitest";

import {
  appendExchange,
  canRate,
  chatHistoryKey,
  nextRating,
  withRating,
  type ChatHistory,
} from "./chat-history";
import type { RagAnswer } from "./rag-ask";

function makeAnswer(answer: string): RagAnswer {
  return {
    answer,
    elapsed_seconds: 1.0,
    processing_time: 1.0,
    sources_used: { pdf_sources: 1 },
    timestamp: "2026-09-12T00:00:00",
    provider: null,
    model: null,
    search_query: null,
  };
}

describe("appendExchange", () => {
  it("adds the question and its answer after what was saved", () => {
    const saved: ChatHistory = {
      messages: [
        { id: 7, role: "user", text: "Q1", answer: null, created_at: "t0" },
        {
          id: 8,
          role: "assistant",
          text: "A1",
          answer: makeAnswer("A1"),
          created_at: "t0",
        },
      ],
    };

    const next = appendExchange(saved, "Q2", makeAnswer("A2"), "t1");

    expect(next.messages.map((m) => [m.role, m.text])).toEqual([
      ["user", "Q1"],
      ["assistant", "A1"],
      ["user", "Q2"],
      ["assistant", "A2"],
    ]);
    expect(next.messages[3].answer?.answer).toBe("A2");
  });

  it("starts from nothing when the history hasn't loaded", () => {
    expect(appendExchange(undefined, "Q", makeAnswer("A"), "t").messages).toHaveLength(2);
  });

  // The refetch replaces these with the saved copies, whose ids are positive.
  it("gives what it adds ids no saved message can have", () => {
    const once = appendExchange(undefined, "Q1", makeAnswer("A1"), "t");
    const twice = appendExchange(once, "Q2", makeAnswer("A2"), "t");
    const ids = twice.messages.map((m) => m.id);

    expect(new Set(ids).size).toBe(4);
    expect(ids.every((id) => id < 0)).toBe(true);
  });

  it("leaves the history it was given alone", () => {
    const saved: ChatHistory = { messages: [] };
    appendExchange(saved, "Q", makeAnswer("A"), "t");
    expect(saved.messages).toEqual([]);
  });
});

describe("chatHistoryKey", () => {
  it("is one key per document", () => {
    expect(chatHistoryKey("doc-a")).toEqual(chatHistoryKey("doc-a"));
    expect(chatHistoryKey("doc-a")).not.toEqual(chatHistoryKey("doc-b"));
  });
});

describe("rating an answer", () => {
  const history: ChatHistory = {
    messages: [
      { id: 1, role: "user", text: "q", answer: null, created_at: "t" },
      { id: 2, role: "assistant", text: "a", answer: null, created_at: "t", rating: null },
      { id: 4, role: "assistant", text: "b", answer: null, created_at: "t", rating: 1 },
    ],
  };

  it("sets the rating of that answer and no other", () => {
    const rated = withRating(history, 2, -1);

    expect(rated.messages.map((m) => m.rating ?? null)).toEqual([null, -1, 1]);
    expect(history.messages[1].rating).toBeNull();
  });

  it("clicking the thumb already chosen takes the rating back", () => {
    expect(nextRating(null, 1)).toBe(1);
    expect(nextRating(1, -1)).toBe(-1);
    expect(nextRating(-1, -1)).toBeNull();
  });

  it("an answer not saved yet can't be rated", () => {
    // `appendExchange`'s stand-ins have negative ids until the refetch.
    expect(canRate({ id: -2, role: "assistant" })).toBe(false);
    expect(canRate({ id: 2, role: "assistant" })).toBe(true);
    expect(canRate({ id: 1, role: "user" })).toBe(false);
  });
});
