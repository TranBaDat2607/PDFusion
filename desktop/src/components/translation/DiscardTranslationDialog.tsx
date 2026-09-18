import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { swapConfirmCopy, type SwapPrompt } from "@/lib/document-swap";

interface DiscardTranslationDialogProps {
  /** The swap waiting on an answer; `null` keeps the dialog closed. */
  prompt: SwapPrompt | null;
  onCancel: () => void;
  /** Cancel the running job, then open `prompt.incomingPath`. */
  onConfirm: (prompt: SwapPrompt) => void;
}

/**
 * Asked before a document swap throws away a running translation (#44).
 *
 * Every way into `openDocument` lands here — the Change PDF button, Ctrl+O, a
 * dropped file, and a path forwarded by the single-instance guard — because
 * only the first of those is a button that could have been disabled instead,
 * and the last is the one where the user never touched the running window.
 */
export function DiscardTranslationDialog({
  prompt,
  onCancel,
  onConfirm,
}: DiscardTranslationDialogProps) {
  const copy = prompt ? swapConfirmCopy(prompt) : null;
  return (
    <Dialog open={prompt !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        {prompt && copy && (
          <>
            <DialogHeader>
              <DialogTitle>{copy.title}</DialogTitle>
              <DialogDescription>{copy.description}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={onCancel}>
                {copy.dismiss}
              </Button>
              <Button variant="destructive" onClick={() => onConfirm(prompt)}>
                {copy.action}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
