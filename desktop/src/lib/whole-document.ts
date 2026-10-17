/**
 * Settings → Chat's "Read a PDF whole" choices: `rag.whole_document_tokens`.
 *
 * A PDF whose text fits the budget is read whole by the answer instead of the
 * five pages retrieval picks — better answers to "summarize this" or "list
 * every table", at several times the tokens a question costs. Labelled in
 * pages, which is what a user knows about a PDF: ~3,000–4,000 characters, or
 * ~1,000 tokens, a dense page. The default is the budget `benchmarks/rag/`
 * measured; the largest is the sidecar's maximum
 * (`config/models.py:WHOLE_DOCUMENT_MAX_TOKENS`), which stays inside a 128k
 * window even for Vietnamese (#106).
 */

export interface WholeDocumentChoice {
  tokens: number;
  label: string;
}

export const WHOLE_DOCUMENT_CHOICES: readonly WholeDocumentChoice[] = [
  { tokens: 0, label: "Off — always answer from the best pages" },
  { tokens: 11_500, label: "Up to ~15 pages (default)" },
  { tokens: 45_000, label: "Up to ~40 pages — more tokens per question" },
  { tokens: 80_000, label: "Up to ~70 pages — many more tokens per question" },
];

/** The choice a saved budget is, or one naming it as it is when the config
 *  file holds a value the menu doesn't offer. */
export function wholeDocumentChoice(tokens: number): WholeDocumentChoice {
  return (
    WHOLE_DOCUMENT_CHOICES.find((c) => c.tokens === tokens) ?? {
      tokens,
      label: `Up to ${tokens.toLocaleString("en-US")} tokens (custom)`,
    }
  );
}
