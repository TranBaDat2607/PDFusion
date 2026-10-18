/**
 * A document's saved chat, as the chat panel shows it (#31).
 *
 * The sidecar saves every answered question in `pdfusion.db`, under the
 * document it was asked about, before it sends the answer. The panel reads
 * that history back by document id and keeps no copy of its own, which is why
 * closing the panel, opening another PDF or restarting no longer loses the
 * conversation.
 */

import type { components } from "@/lib/api-types";
import type { RagAnswer } from "@/lib/rag-ask";

export type ChatMessage = components["schemas"]["ChatMessageResponse"];
export type ChatHistory = components["schemas"]["ChatHistoryResponse"];

/** The query key of one document's history. By id, never by path: two copies
 *  of one PDF share a conversation, and two different `paper.pdf`s don't. */
export function chatHistoryKey(documentId: string | null) {
  return ["rag", "messages", documentId] as const;
}

/** The query key of Settings → Chat's document list, which counts each
 *  document's questions. */
export const CHAT_DOCUMENTS_KEY = ["rag", "documents"] as const;

/**
 * The history with a just-answered question appended, shown until the refetch
 * returns the saved copy. Its ids are negative, so they can't collide with the
 * database's.
 */
export function appendExchange(
  history: ChatHistory | undefined,
  question: string,
  answer: RagAnswer,
  createdAt: string,
): ChatHistory {
  const messages = history?.messages ?? [];
  const lowest = Math.min(0, ...messages.map((m) => m.id));
  return {
    messages: [
      ...messages,
      {
        id: lowest - 1,
        role: "user",
        text: question,
        answer: null,
        created_at: createdAt,
      },
      {
        id: lowest - 2,
        role: "assistant",
        text: answer.answer,
        answer,
        created_at: createdAt,
      },
    ],
  };
}

/**
 * The conversation with a just-answered question on the end, for the render
 * that sees the answer before the history cache does. Without it the panel
 * drops its draft and pending question for a frame, shrinks, and the browser
 * clamps a reader who had scrolled up to the new bottom (#109).
 *
 * The answer is matched by identity: `appendExchange` stores the very object
 * it is given, so once the cache holds the stand-in, nothing is added twice.
 */
export function exchangeShown(
  saved: ChatMessage[],
  question: string,
  answer: RagAnswer,
): ChatMessage[] {
  if (saved[saved.length - 1]?.answer === answer) return saved;
  return appendExchange({ messages: saved }, question, answer, "").messages;
}

/** The user's verdict on an answer: helpful, wrong, or none. Optional on the
 *  wire, so the schema's type carries `undefined`; a verdict never does. */
export type Rating = Exclude<components["schemas"]["RateAnswerRequest"]["rating"], undefined>;

/** A thumb: the verdict a click gives. */
export type Thumb = NonNullable<Rating>;

/** The history with one answer's rating changed: what the panel shows while
 *  the sidecar saves it. */
export function withRating(
  history: ChatHistory,
  messageId: number,
  rating: Rating,
): ChatHistory {
  return {
    messages: history.messages.map((m) =>
      m.id === messageId ? { ...m, rating } : m,
    ),
  };
}

/** A thumb toggles: clicking the one already chosen takes the rating back. */
export function nextRating(current: Rating | undefined, clicked: Thumb): Rating {
  return current === clicked ? null : clicked;
}

/** Only a saved answer can be rated. `appendExchange`'s stand-ins have
 *  negative ids until the refetch brings the saved copy. */
export function canRate(message: Pick<ChatMessage, "id" | "role">): boolean {
  return message.role === "assistant" && message.id > 0;
}
