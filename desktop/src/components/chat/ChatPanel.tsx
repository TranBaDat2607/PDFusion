import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { MessageSquare, Sparkles, Trash2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ActionLog } from "@/components/chat/ActionLog";
import { AssistantMessage } from "@/components/chat/AssistantMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { UserMessage } from "@/components/chat/UserMessage";
import { useRagAsk, type RagAnswer } from "@/hooks/useRagAsk";
import { useRagIndex } from "@/hooks/useRagIndex";
import { useUpdateConfig } from "@/hooks/useConfig";
import { useAppStore } from "@/lib/store";

interface ChatPanelProps {
  documentPath: string | null;
  onJumpToPage?: (page: number) => void;
  showing?: boolean;
}

interface ChatMessage {
  id: number;
  kind: "user" | "assistant";
  text?: string;
  answer?: RagAnswer;
}

let messageCounter = 0;

export function ChatPanel({
  documentPath,
  onJumpToPage,
  showing = true,
}: ChatPanelProps) {
  const index = useRagIndex();
  const ask = useRagAsk();
  const update = useUpdateConfig();
  const setChatOpen = useAppStore((s) => s.setChatOpen);
  const setRagEnabled = useAppStore((s) => s.setRagEnabled);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);

  const handleClose = useCallback(() => {
    setChatOpen(false);
    setRagEnabled(false);
    update.mutate({ rag_enabled: false });
  }, [setChatOpen, setRagEnabled, update]);

  // Auto-index when a document is loaded
  useEffect(() => {
    if (!documentPath) {
      index.reset();
      ask.reset();
      setMessages([]);
      return;
    }
    void index.start(documentPath);
    setMessages([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentPath]);

  // When ask finishes, append the assistant message
  useEffect(() => {
    if (ask.state.status === "done" && ask.state.answer) {
      setMessages((prev) => [
        ...prev,
        { id: ++messageCounter, kind: "assistant", answer: ask.state.answer! },
      ]);
      setPendingQuestion(null);
    }
  }, [ask.state.status, ask.state.answer]);

  // Auto-scroll to bottom when content changes
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length, ask.state.actions.length, ask.state.status]);

  const handleSubmit = useCallback(
    ({
      text,
      includeWebResearch,
      useDeepSearch,
    }: {
      text: string;
      includeWebResearch: boolean;
      useDeepSearch: boolean;
    }) => {
      setMessages((prev) => [
        ...prev,
        { id: ++messageCounter, kind: "user", text },
      ]);
      setPendingQuestion(text);
      void ask.ask({
        question: text,
        documentId: index.state.documentId,
        includeWebResearch,
        useDeepSearch,
      });
    },
    [ask, index.state.documentId],
  );

  const inputDisabled = !documentPath || index.state.status !== "ready";

  return (
    <AnimatePresence>
      {showing && (
        <motion.div
          key="chat-root"
          className="flex h-full w-full flex-col"
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 24 }}
          transition={{ duration: 0.18 }}
        >
      <header className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold">AI Chat</span>
          {index.state.status === "ready" && index.state.chunks !== null && (
            <Badge variant="outline" className="font-mono text-[10px]">
              {index.state.chunks} chunks
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setMessages([])}
            disabled={messages.length === 0}
          >
            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
            Clear
          </Button>
          <Button
            size="icon"
            variant="ghost"
            onClick={handleClose}
            aria-label="Close chat"
            className="h-8 w-8"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </header>

      {index.state.status === "indexing" && (
        <div className="border-b border-border bg-muted/40 px-4 py-2">
          <div className="mb-1 flex items-center justify-between text-xs">
            <span className="text-muted-foreground">{index.state.stage}</span>
            <span className="font-mono">{Math.round(index.state.progress)}%</span>
          </div>
          <Progress value={index.state.progress} className="h-1" />
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3">
        {messages.length === 0 && index.state.status !== "indexing" && (
          <EmptyState ready={index.state.status === "ready"} />
        )}

        <div className="space-y-3">
          <AnimatePresence initial={false}>
            {messages.map((m) => (
              <motion.div
                key={m.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
              >
                {m.kind === "user" && m.text && <UserMessage text={m.text} />}
                {m.kind === "assistant" && m.answer && (
                  <AssistantMessage
                    answer={m.answer}
                    onJumpToPage={onJumpToPage}
                  />
                )}
              </motion.div>
            ))}
          </AnimatePresence>

          {ask.state.status === "asking" && (
            <div className="space-y-2">
              {pendingQuestion && (
                <div className="text-xs text-muted-foreground">
                  Working on: <span className="italic">{pendingQuestion}</span>
                </div>
              )}
              <ActionLog
                actions={ask.state.actions}
                busy={true}
                currentMessage={ask.state.message}
              />
            </div>
          )}
          {ask.state.status === "error" && ask.state.error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
              {ask.state.error}
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-border bg-background p-3">
        <ChatInput
          onSubmit={handleSubmit}
          disabled={inputDisabled}
          busy={ask.state.status === "asking"}
        />
      </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function EmptyState({ ready }: { ready: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <div className="rounded-full bg-primary/10 p-3">
        <Sparkles className="h-5 w-5 text-primary" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">Ask the document</p>
        <p className="text-xs text-muted-foreground max-w-[260px]">
          {ready
            ? "Type a question about the loaded PDF. Toggle Web research or Deep search for richer answers."
            : "Open a PDF to start indexing. Then ask away."}
        </p>
      </div>
    </div>
  );
}

