import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import {
  CHAT_DOCUMENTS_KEY,
  chatHistoryKey,
  type ChatHistory,
} from "@/lib/chat-history";

function messagesPath(documentId: string): string {
  return `/rag/document/${encodeURIComponent(documentId)}/messages`;
}

/** One document's saved chat, oldest first. Nothing is fetched until the
 *  document's id is known. */
export function useChatHistory(documentId: string | null) {
  return useQuery({
    queryKey: chatHistoryKey(documentId),
    queryFn: () => api.get<ChatHistory>(messagesPath(documentId as string)),
    enabled: documentId !== null,
  });
}

/** Delete one document's saved chat. */
export function useClearChatHistory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.delete<void>(messagesPath(documentId)),
    onSuccess: (_data, documentId) => {
      queryClient.setQueryData<ChatHistory>(chatHistoryKey(documentId), {
        messages: [],
      });
      void queryClient.invalidateQueries({ queryKey: CHAT_DOCUMENTS_KEY });
    },
  });
}
