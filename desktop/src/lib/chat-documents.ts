/**
 * Settings → Chat's list of recorded documents (#31).
 *
 * Pure, so the `node`-environment vitest suite can hold each row to what the
 * sidecar reported.
 */

import type { components } from "@/lib/api-types";
import { formatBytes } from "@/lib/export-pdf";

export type ChatDocument = components["schemas"]["DocumentSummaryResponse"];

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The line under a document's name: its pages and size, whether a question
 *  can be answered from it yet, and how many have been asked. */
export function describeDocument(document: ChatDocument): string {
  const parts: string[] = [];
  if (document.page_count != null) {
    parts.push(count(document.page_count, "page", "pages"));
  }
  parts.push(formatBytes(document.size_bytes));
  parts.push(
    document.chunk_count != null
      ? `indexed, ${count(document.chunk_count, "chunk", "chunks")}`
      : "not indexed",
  );
  parts.push(
    document.question_count === 0
      ? "no questions"
      : count(document.question_count, "question", "questions"),
  );
  return parts.join(" · ");
}
