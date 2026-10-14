/**
 * The page a chat answer's citation link points at.
 *
 * `rag/citations.py` checks every `[p. N]` the answer model writes against the
 * pages it was shown, and rewrites the ones that pass as Markdown links to
 * `#page=N`. `AssistantMessage` renders those as jumps in the viewer; any
 * other link is left alone. Pure, so it is tested under vitest's node env.
 */

const CITATION_HREF = /^#page=([1-9]\d*)$/;

export function pageFromCitationHref(href: string | undefined): number | undefined {
  const match = href ? CITATION_HREF.exec(href) : null;
  return match ? Number(match[1]) : undefined;
}
