import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api-client";
import {
  CHAT_DOCUMENTS_KEY,
  chatHistoryKey,
  withRating,
  type ChatHistory,
  type Rating,
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

/** Rate one of a document's answers. Shown at once; put back as it was if
 *  the sidecar refuses. */
export function useRateAnswer(documentId: string | null) {
  const queryClient = useQueryClient();
  const key = chatHistoryKey(documentId);
  return useMutation({
    mutationFn: ({ messageId, rating }: { messageId: number; rating: Rating }) =>
      api.put<void>(`${messagesPath(documentId as string)}/${messageId}/rating`, { rating }),
    onMutate: async ({ messageId, rating }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const before = queryClient.getQueryData<ChatHistory>(key);
      if (before) queryClient.setQueryData(key, withRating(before, messageId, rating));
      return { before };
    },
    onError: (_error, _vars, context) => {
      if (context?.before) queryClient.setQueryData(key, context.before);
    },
  });
}
