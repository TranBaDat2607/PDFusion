import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "motion/react";
import { MessageSquare, Sparkles, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ActionLog } from "@/components/chat/ActionLog";
import { AssistantMessage } from "@/components/chat/AssistantMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { UserMessage } from "@/components/chat/UserMessage";
import { useChatHistory, useClearChatHistory } from "@/hooks/useChatHistory";
import { useRagAsk } from "@/hooks/useRagAsk";
import { useRagIndex } from "@/hooks/useRagIndex";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useConfig, type ConfigResponse } from "@/hooks/useConfig";
import {
  CHAT_DOCUMENTS_KEY,
  appendExchange,
  chatHistoryKey,
  type ChatHistory,
  type ChatMessage,
} from "@/lib/chat-history";
import { answerForDocument, needsReindex } from "@/lib/rag-ask";
import { chatModel } from "@/lib/model-choice";
import { useProviders, type ProviderInfo } from "@/hooks/useProviders";
import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";

interface ChatPanelProps {
  documentPath: string | null;
  onJumpToPage?: (page: number) => void;
  showing?: boolean;
}

export function ChatPanel({
  documentPath,
  onJumpToPage,
  showing = true,
}: ChatPanelProps) {
  const index = useRagIndex();
  const ask = useRagAsk();
  const { data: config } = useConfig();
  const { data: providers } = useProviders();
  const queryClient = useQueryClient();
  // Saved by the sidecar and read back by document id, so a conversation
  // survives the panel closing, another PDF being opened, and a restart (#31).
  const history = useChatHistory(index.state.documentId);
  const clearHistory = useClearChatHistory();
  const setChatOpen = useAppStore((s) => s.setChatOpen);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);

  const saved = history.data?.messages ?? [];
  // A question being answered, or one that failed, shows after the saved
  // conversation. It takes the position its saved copy will have, so nothing
  // re-animates when that copy arrives.
  const showPending =
    pendingQuestion !== null &&
    (ask.state.status === "asking" || ask.state.status === "error");
  const messages: ChatMessage[] = showPending
    ? [
        ...saved,
        {
          id: 0,
          role: "user",
          text: pendingQuestion,
          answer: null,
          created_at: "",
        },
      ]
    : saved;

  // Hides the panel and nothing more. Settings → Chat is where chat is
  // turned off (#32).
  const handleClose = useCallback(() => setChatOpen(false), [setChatOpen]);

  // A new document has its own conversation, and indexes itself. Whatever the
  // panel was asking belongs to the previous document, so it is aborted first:
  // left running, its answer landed in this document's chat, with page links
  // into the wrong PDF (#59). The sidecar still saves that answer under the
  // document it was about.
  useEffect(() => {
    ask.reset();
    setPendingQuestion(null);
    if (!documentPath) {
      index.reset();
      return;
    }
    void index.start(documentPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentPath]);

  // When an answer about the open document arrives, show it at once. The
  // sidecar saved it before sending it, and the refetch brings that copy.
  useEffect(() => {
    const answer = answerForDocument(ask.state, index.state.documentId);
    const documentId = ask.state.documentId;
    if (!answer || !documentId) return;
    const key = chatHistoryKey(documentId);
    if (pendingQuestion !== null) {
      queryClient.setQueryData<ChatHistory>(key, (current) =>
        appendExchange(
          current,
          pendingQuestion,
          answer,
          new Date().toISOString(),
        ),
      );
    }
    void queryClient.invalidateQueries({ queryKey: key });
    void queryClient.invalidateQueries({ queryKey: CHAT_DOCUMENTS_KEY });
    setPendingQuestion(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask.state.status, ask.state.answer]);

  // The question failed because this document's chat index is missing, or was
  // damaged and has been cleared: index the document again (#31).
  useEffect(() => {
    if (documentPath && needsReindex(ask.state, index.state.documentId)) {
      void index.start(documentPath);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask.state.status, ask.state.errorCode]);

  // Auto-scroll to bottom when content changes
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length, ask.state.actions.length, ask.state.status]);

  const handleSubmit = useCallback(
    ({ text }: { text: string }) => {
      const documentId = index.state.documentId;
      if (!documentId) return;
      setPendingQuestion(text);
      // The toolbar's "To" language, which is also what Translate sends.
      void ask.ask({
        question: text,
        documentId,
        targetLang: config?.translation.default_target_lang,
      });
    },
    [ask, index.state.documentId, config?.translation.default_target_lang],
  );

  const handleClear = useCallback(() => {
    const documentId = index.state.documentId;
    if (!documentId) return;
    clearHistory.mutate(documentId, {
      onError: (e) =>
        toast.error("Could not clear the conversation", {
          description: (e as Error).message,
        }),
    });
  }, [clearHistory, index.state.documentId]);

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
          {config && providers && (
            <AnswerModelBadge config={config} providers={providers} />
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={handleClear}
            disabled={saved.length === 0 || clearHistory.isPending}
          >
            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
            Clear
          </Button>
          <Button
            size="icon"
            variant="ghost"
            onClick={handleClose}
            aria-label="Hide chat"
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
            {messages.map((m, position) => (
              // Keyed by position: the copy shown while a new answer is saved
              // and the saved copy the refetch brings differ only in their ids.
              <motion.div
                key={position}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
              >
                {m.role === "user" && <UserMessage text={m.text} />}
                {m.role === "assistant" && m.answer && (
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

/** Which model writes the answers. It follows the translation choice but
 *  falls back to any LLM with a key, so it isn't always the toolbar's. */
function AnswerModelBadge({
  config,
  providers,
}: {
  config: ConfigResponse;
  providers: ProviderInfo[];
}) {
  const answering = chatModel(config, providers);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          tabIndex={0}
          className={cn(
            "max-w-[160px] truncate font-mono text-[10px]",
            !answering && "border-amber-500/60 text-amber-600 dark:text-amber-400",
          )}
        >
          {answering ? answering.model : "no model"}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        {answering
          ? `${answering.label} writes the answers.`
          : "No API key is saved, so answers are excerpts from the document. Add a key in Settings for written answers."}
      </TooltipContent>
    </Tooltip>
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
            ? "Type a question about the loaded PDF. Answers cite the pages they came from."
            : "Open a PDF to start indexing. Then ask away."}
        </p>
      </div>
    </div>
  );
}
