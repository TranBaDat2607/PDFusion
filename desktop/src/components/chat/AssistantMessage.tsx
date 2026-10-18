import { memo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import { ReferenceList } from "@/components/chat/ReferenceList";
import { Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import type { RagAnswer } from "@/hooks/useRagAsk";
import type { Rating, Thumb } from "@/lib/chat-history";
import { pageFromCitationHref } from "@/lib/citation-links";
import { splitDraftBlocks } from "@/lib/draft-blocks";
import { toReferenceRows } from "@/lib/rag-references";

interface AssistantMessageProps {
  answer: RagAnswer;
  /** Which model wrote it ("Claude · claude-opus-5"); null when none did. */
  byline?: string | null;
  onJumpToPage?: (page: number) => void;
  /** The user's rating; `undefined` when the answer can't be rated yet. */
  rating?: Rating;
  onRate?: (clicked: Thumb) => void;
}

export function AssistantMessage({
  answer,
  byline,
  onJumpToPage,
  rating,
  onRate,
}: AssistantMessageProps) {
  const references = toReferenceRows(answer);
  const cited = references.filter((item) => item.cited).length;
  return (
    <div className="flex w-full max-w-[95%] gap-3 rounded-lg border-l-2 border-primary bg-card/50 p-4">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15">
        <Sparkles className="h-3.5 w-3.5 text-primary" />
      </div>
      <div className="flex-1 space-y-3 overflow-hidden">
        <div className="prose prose-sm dark:prose-invert max-w-none break-words">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[rehypeKatex]}
            components={{
              // `[p. N](#page=N)` is a citation the sidecar checked against
              // the pages the model was shown (`rag/citations.py`): a jump in
              // the viewer, never a navigation of the webview.
              // `node` is react-markdown's hast node, not a DOM attribute.
              a: ({ node: _node, href, children, ...rest }) => {
                const page = pageFromCitationHref(href);
                if (page === undefined) {
                  return (
                    <a href={href} {...rest}>
                      {children}
                    </a>
                  );
                }
                return (
                  <a
                    href={href}
                    onClick={(event) => {
                      event.preventDefault();
                      onJumpToPage?.(page);
                    }}
                    className="whitespace-nowrap rounded bg-primary/10 px-1 text-[0.85em] font-medium text-primary no-underline hover:bg-primary/20"
                    title={`Go to page ${page}`}
                  >
                    {children}
                  </a>
                );
              },
            }}
          >
            {answer.answer}
          </ReactMarkdown>
        </div>
        {answer.search_query && (
          // A follow-up is searched for as its standalone rewrite; when the
          // sources look wrong, this is what to check first.
          <p className="text-[11px] text-muted-foreground">
            Searched for: <span className="italic">{answer.search_query}</span>
          </p>
        )}
        {references.length > 0 && (
          <ReferenceList
            title={
              cited > 0
                ? `Sources: ${cited} cited, ${references.length} read`
                : `Sources (${references.length})`
            }
            items={references}
            onItemClick={(item) => item.page && onJumpToPage?.(item.page)}
          />
        )}
        <div className="flex items-center gap-2">
          {byline && (
            <p className="font-mono text-[10px] text-muted-foreground">{byline}</p>
          )}
          {onRate && rating !== undefined && (
            <div className="ml-auto flex gap-1">
              <button
                type="button"
                onClick={() => onRate(1)}
                aria-pressed={rating === 1}
                title="Helpful answer"
                className={`rounded p-1 hover:bg-muted ${rating === 1 ? "text-primary" : "text-muted-foreground"}`}
              >
                <ThumbsUp className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => onRate(-1)}
                aria-pressed={rating === -1}
                title="Wrong or unhelpful answer"
                className={`rounded p-1 hover:bg-muted ${rating === -1 ? "text-destructive" : "text-muted-foreground"}`}
              >
                <ThumbsDown className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const DRAFT_REMARK_PLUGINS = [remarkGfm, remarkMath];
const DRAFT_REHYPE_PLUGINS = [rehypeKatex];
// Unchecked text: render a link the model wrote as text, never as something
// the webview could navigate to.
const DRAFT_COMPONENTS = {
  a: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
};

// Memoized on its text: a finished block never changes, so once rendered it is
// not parsed again (#113). The constants above keep the other props equal too.
const DraftBlock = memo(function DraftBlock({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={DRAFT_REMARK_PLUGINS}
      rehypePlugins={DRAFT_REHYPE_PLUGINS}
      components={DRAFT_COMPONENTS}
    >
      {text}
    </ReactMarkdown>
  );
});

/**
 * The answer while the model is still writing it (`answer_delta`). Plain
 * Markdown and no sources: its `[p. N]` citations have not been checked yet,
 * so none of them is a link until the finished answer replaces this.
 *
 * Only the unfinished tail is parsed on each update; the blocks before it are
 * rendered once. Parsing the whole draft every time made the work grow with
 * the square of the answer's length (#113). Each block is its own Markdown
 * document, so what spans blocks (a numbered list broken by blank lines, a link
 * reference) can differ until the finished answer replaces the draft.
 */
export function DraftMessage({ text }: { text: string }) {
  const { blocks, tail } = splitDraftBlocks(text);
  return (
    <div className="flex w-full max-w-[95%] gap-3 rounded-lg border-l-2 border-primary/50 bg-card/50 p-4">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15">
        <Sparkles className="h-3.5 w-3.5 animate-pulse text-primary" />
      </div>
      <div className="prose prose-sm dark:prose-invert max-w-none flex-1 overflow-hidden break-words">
        {blocks.map((block, i) => (
          <DraftBlock key={i} text={block} />
        ))}
        {tail && <DraftBlock text={tail} />}
      </div>
    </div>
  );
}
