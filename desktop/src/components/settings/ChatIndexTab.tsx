import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  Loader2,
  MessageSquare,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-types";
import { describeDocument, type ChatDocument } from "@/lib/chat-documents";
import {
  CHAT_DOCUMENTS_KEY,
  chatHistoryKey,
  type ChatHistory,
} from "@/lib/chat-history";

type DocumentList = components["schemas"]["DocumentListResponse"];
type ResetResult = components["schemas"]["ResetIndexesResponse"];

type Confirmation =
  | { kind: "remove"; document: ChatDocument }
  | { kind: "reset" };

/**
 * Settings → Chat: every document chat has recorded, with Remove, and Reset for
 * a damaged chat index (#31). The list is read from the records alone, so
 * opening this tab never loads the embedding model.
 */
export function ChatIndexTab({ open }: { open: boolean }) {
  const queryClient = useQueryClient();
  // Kept after the dialog closes, so its text doesn't change mid-animation.
  const [confirmation, setConfirmation] = useState<Confirmation>({
    kind: "reset",
  });
  const [confirming, setConfirming] = useState(false);

  const documents = useQuery({
    queryKey: CHAT_DOCUMENTS_KEY,
    queryFn: () => api.get<DocumentList>("/rag/documents"),
    enabled: open,
  });

  const remove = useMutation({
    mutationFn: (document: ChatDocument) =>
      api.delete<void>(
        `/rag/document/${encodeURIComponent(document.document_id)}`,
      ),
    onSuccess: (_data, document) => {
      toast.success(`Removed ${document.display_name}`);
      // A chat panel showing this document now shows an empty conversation.
      queryClient.setQueryData<ChatHistory>(
        chatHistoryKey(document.document_id),
        { messages: [] },
      );
      void queryClient.invalidateQueries({ queryKey: CHAT_DOCUMENTS_KEY });
    },
    onError: (e) =>
      toast.error("Could not remove the document", {
        description: (e as Error).message,
      }),
  });

  const reset = useMutation({
    mutationFn: () => api.post<ResetResult>("/rag/reset"),
    onSuccess: (result) => {
      toast.success(
        result.removed === 1
          ? "Deleted 1 chat index"
          : `Deleted ${result.removed} chat indexes`,
      );
      void queryClient.invalidateQueries({ queryKey: CHAT_DOCUMENTS_KEY });
    },
    onError: (e) =>
      toast.error("Could not reset the chat indexes", {
        description: (e as Error).message,
      }),
  });

  const confirm = (next: Confirmation) => {
    setConfirmation(next);
    setConfirming(true);
  };

  const handleConfirmed = () => {
    if (confirmation.kind === "remove") remove.mutate(confirmation.document);
    else reset.mutate();
    setConfirming(false);
  };

  const list = documents.data?.documents ?? [];

  return (
    <div className="space-y-4 py-2">
      <div className="space-y-3 rounded-md border border-border bg-muted/40 p-4">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">Chat documents</span>
        </div>
        <p className="text-sm leading-relaxed text-muted-foreground">
          Every PDF you've chatted about, with its chat index and saved
          conversation. Stored on this computer and never uploaded.
        </p>
      </div>

      {documents.isError ? (
        <div className="text-sm text-destructive">
          Could not load the documents: {documents.error.message}
        </div>
      ) : documents.isPending ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : list.length === 0 ? (
        <div className="text-sm text-muted-foreground">
          No documents yet. Turn on chat and open a PDF to add one.
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {list.map((document) => (
            <li
              key={document.document_id}
              className="flex items-center gap-3 px-3 py-2"
            >
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div
                  className="truncate text-sm font-medium"
                  title={document.path ?? document.display_name}
                >
                  {document.display_name}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {describeDocument(document)}
                </div>
              </div>
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8 shrink-0"
                aria-label={`Remove ${document.display_name}`}
                disabled={remove.isPending}
                onClick={() => confirm({ kind: "remove", document })}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void documents.refetch()}
          disabled={documents.isFetching}
        >
          {documents.isFetching ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Refreshing…
            </>
          ) : (
            "Refresh"
          )}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => confirm({ kind: "reset" })}
          disabled={reset.isPending}
        >
          {reset.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RotateCcw className="mr-2 h-4 w-4" />
          )}
          Reset chat indexes
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Reset deletes every chat index, which repairs a damaged one.
        Conversations are kept, and each PDF is indexed again the next time you
        chat about it.
      </p>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirmation.kind === "remove"
                ? `Remove ${confirmation.document.display_name}?`
                : "Reset chat indexes?"}
            </DialogTitle>
            <DialogDescription>
              {confirmation.kind === "remove"
                ? "This deletes its chat index and its saved conversation. The PDF file itself is not touched. If it's open, chat indexes it again the next time you ask about it."
                : "This deletes every document's chat index. Saved conversations are kept, and each PDF is indexed again the next time you chat about it."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleConfirmed}>
              {confirmation.kind === "remove" ? "Remove" : "Reset"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
