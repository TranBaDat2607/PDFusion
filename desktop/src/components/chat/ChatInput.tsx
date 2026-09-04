import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ChatInputProps {
  onSubmit: (params: { text: string }) => void;
  disabled?: boolean;
  busy?: boolean;
}

export function ChatInput({ onSubmit, disabled, busy }: ChatInputProps) {
  const [text, setText] = useState("");
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
    onSubmit({ text: trimmed });
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
      <div className="flex items-center text-xs text-muted-foreground">
        <span className="ml-auto opacity-70">Enter to send · Shift+Enter for new line</span>
      </div>
    </div>
  );
}
