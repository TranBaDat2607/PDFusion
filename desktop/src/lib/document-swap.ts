/**
 * Opening a document while another one is being translated (#44).
 *
 * A translation is a job on the sidecar, not a piece of React state: dropping
 * the `job_id` on the floor leaves it running, billing an LLM and streaming
 * events for a document nobody is looking at. So a swap has to be asked about
 * and then actually cancelled, and the question reaches the user the same way
 * whichever entry point they came in through — the Change PDF button, Ctrl+O,
 * a dropped file, or a path forwarded by the single-instance guard.
 *
 * Pure, so it runs under vitest's node environment; the dialog is a dumb
 * component over `swapConfirmCopy`.
 */

import { basename } from "@/lib/export-pdf";

export interface SwapPrompt {
  /** The document the user is trying to open. */
  incomingPath: string;
  /** The one currently open — the translation about to be discarded is its. */
  currentPath: string | null;
  /** Percent complete when the swap was asked for, for the dialog's copy. */
  progress: number;
}

/**
 * Whether opening `incomingPath` has to ask first.
 *
 * Not busy means nothing is at stake. Re-opening the document that is *already*
 * being translated is deliberately not a question either: an "Open with
 * PDFusion" on the file the user is watching should leave its run alone, not
 * offer to kill it.
 */
export function shouldConfirmSwap(
  busy: boolean,
  incomingPath: string,
  currentPath: string | null,
): boolean {
  if (!busy) return false;
  return incomingPath !== currentPath;
}

/** What the "discard the running translation?" dialog says, and what its
 *  buttons do. */
export function swapConfirmCopy(prompt: SwapPrompt): {
  title: string;
  description: string;
  action: string;
  dismiss: string;
} {
  const incoming = basename(prompt.incomingPath);
  const current = prompt.currentPath ? basename(prompt.currentPath) : null;
  const percent = Math.max(0, Math.min(100, Math.round(prompt.progress)));

  // Below 1% there is no progress worth naming, and "0% translated" reads as
  // though the run had stalled.
  const how = current
    ? percent >= 1
      ? `${current} is ${percent}% translated.`
      : `${current} is still being translated.`
    : "A translation is still running.";

  return {
    title: "Discard the running translation?",
    description:
      `${how} Opening ${incoming} cancels that run, and the pages ` +
      `translated so far are lost — nothing is saved until you export it.`,
    action: `Discard and open ${incoming}`,
    dismiss: "Keep translating",
  };
}
