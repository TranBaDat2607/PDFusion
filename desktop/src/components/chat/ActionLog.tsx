import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import type { ActionEvent } from "@/hooks/useRagAsk";

interface ActionLogProps {
  actions: ActionEvent[];
  busy: boolean;
  currentMessage?: string;
}

export function ActionLog({ actions, busy, currentMessage }: ActionLogProps) {
  if (!busy && actions.length === 0) return null;
  return (
    <div className="space-y-1 rounded-md border border-border/40 bg-muted/30 px-3 py-2 text-xs">
      {actions.map((a) => (
        <div key={a.id} className="flex items-center gap-2 text-muted-foreground">
          {a.status === "running" && (
            <Loader2 className="h-3 w-3 animate-spin text-primary" />
          )}
          {a.status === "done" && (
            <CheckCircle2 className="h-3 w-3 text-primary" />
          )}
          {a.status === "failed" && (
            <XCircle className="h-3 w-3 text-destructive" />
          )}
          <span className="truncate">{a.description}</span>
        </div>
      ))}
      {busy && currentMessage && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin text-primary" />
          <span className="truncate">{currentMessage}</span>
        </div>
      )}
    </div>
  );
}
