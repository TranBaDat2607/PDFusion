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
