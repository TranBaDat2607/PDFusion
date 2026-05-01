import { useEffect, useRef, useState } from "react";
import { Globe, Send, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface ChatInputProps {
  onSubmit: (params: {
    text: string;
    includeWebResearch: boolean;
    useDeepSearch: boolean;
  }) => void;
  disabled?: boolean;
  busy?: boolean;
}

export function ChatInput({ onSubmit, disabled, busy }: ChatInputProps) {
  const [text, setText] = useState("");
  const [web, setWeb] = useState(false);
  const [deep, setDeep] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow up to ~4 lines
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!text) {
      el.style.height = "";
      return;
    }
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [text]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || busy) return;
    onSubmit({ text: trimmed, includeWebResearch: web, useDeepSearch: deep });
    setText("");
  };

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-2 rounded-lg border border-border bg-background p-2 focus-within:border-ring">
        <Textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={
            disabled
              ? "Load and index a PDF to start chatting…"
              : "Ask anything about this document…"
          }
          disabled={disabled}
          rows={1}
          className="min-h-[36px] resize-none border-none bg-transparent p-0 text-sm shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <Button
          size="icon"
          onClick={submit}
          disabled={!text.trim() || disabled || busy}
          aria-label="Send"
          className="h-8 w-8 shrink-0"
        >
          <Send className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => setWeb((v) => !v)}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 transition-colors ${
                web
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border hover:bg-accent"
              }`}
            >
              <Globe className="h-3 w-3" />
              Web research
            </button>
          </TooltipTrigger>
          <TooltipContent>Augment answers with live web sources</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => setDeep((v) => !v)}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 transition-colors ${
                deep
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border hover:bg-accent"
              }`}
            >
              <Sparkles className="h-3 w-3" />
              Deep search
            </button>
          </TooltipTrigger>
          <TooltipContent>
            Multi-hop academic citation search (PubMed, arXiv, Semantic Scholar)
          </TooltipContent>
        </Tooltip>
        <span className="ml-auto opacity-70">Enter to send · Shift+Enter for new line</span>
      </div>
    </div>
  );
}
