import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import { ReferenceList } from "@/components/chat/ReferenceList";
import { Sparkles } from "lucide-react";
import type { RagAnswer } from "@/hooks/useRagAsk";

interface AssistantMessageProps {
  answer: RagAnswer;
  onJumpToPage?: (page: number) => void;
}

export function AssistantMessage({ answer, onJumpToPage }: AssistantMessageProps) {
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
        {answer.pdf_sources && answer.pdf_sources.length > 0 && (
          <ReferenceList
            title={`PDF references (${answer.pdf_sources.length})`}
            items={answer.pdf_sources.map((s, i) => ({
              key: `pdf-${i}`,
              label: `Page ${s.page ?? "?"}`,
              detail: (s.text ?? "").slice(0, 180),
              page: s.page,
            }))}
            onItemClick={(item) => item.page && onJumpToPage?.(item.page)}
          />
        )}
        {answer.web_sources && answer.web_sources.length > 0 && (
          <ReferenceList
            title={`Web sources (${answer.web_sources.length})`}
            items={answer.web_sources.map((s, i) => ({
              key: `web-${i}`,
              label: s.title || s.url || "Source",
              detail: s.snippet ?? s.url ?? "",
              url: s.url,
            }))}
            onItemClick={(item) => {
              if (item.url) void openExternal(item.url);
            }}
          />
        )}
      </div>
    </div>
  );
}

async function openExternal(url: string) {
  try {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } catch {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
