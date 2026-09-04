import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import { ReferenceList } from "@/components/chat/ReferenceList";
import { Sparkles } from "lucide-react";
import type { RagAnswer } from "@/hooks/useRagAsk";
import { toReferenceRows } from "@/lib/rag-references";

interface AssistantMessageProps {
  answer: RagAnswer;
  onJumpToPage?: (page: number) => void;
}

export function AssistantMessage({ answer, onJumpToPage }: AssistantMessageProps) {
  const references = toReferenceRows(answer);
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
          >
            {answer.answer}
          </ReactMarkdown>
        </div>
        {references.length > 0 && (
          <ReferenceList
            title={`PDF references (${references.length})`}
            items={references}
            onItemClick={(item) => item.page && onJumpToPage?.(item.page)}
          />
        )}
      </div>
    </div>
  );
}
