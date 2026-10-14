import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import { ReferenceList } from "@/components/chat/ReferenceList";
import { Sparkles } from "lucide-react";
import type { RagAnswer } from "@/hooks/useRagAsk";
import { pageFromCitationHref } from "@/lib/citation-links";
import { toReferenceRows } from "@/lib/rag-references";

interface AssistantMessageProps {
  answer: RagAnswer;
  /** Which model wrote it ("Claude · claude-opus-5"); null when none did. */
  byline?: string | null;
  onJumpToPage?: (page: number) => void;
}

export function AssistantMessage({ answer, byline, onJumpToPage }: AssistantMessageProps) {
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
              a: ({ href, children, ...rest }) => {
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
        {byline && (
          <p className="font-mono text-[10px] text-muted-foreground">{byline}</p>
        )}
      </div>
    </div>
  );
}
