import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { pageLimitCopy, type PageLimitCheck } from "@/lib/page-range";

export type OverLimit = Extract<PageLimitCheck, { over: true }>;

interface PageLimitDialogProps {
  /** The check that stopped a Translate click; `null` keeps the dialog closed. */
  check: OverLimit | null;
  onCancel: () => void;
  /** Translate `check.suggestion` instead. */
  onConfirm: (check: OverLimit) => void;
}

/**
 * Asked instead of failing when a Translate click covers more pages than one
 * translation may (#33). The sidecar would refuse the same request with 422;
 * this offers the part that fits.
 */
export function PageLimitDialog({ check, onCancel, onConfirm }: PageLimitDialogProps) {
  const copy = check ? pageLimitCopy(check) : null;
  return (
    <Dialog open={check !== null} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        {check && copy && (
          <>
            <DialogHeader>
              <DialogTitle>{copy.title}</DialogTitle>
              <DialogDescription>{copy.description}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={onCancel}>
                Cancel
              </Button>
              <Button onClick={() => onConfirm(check)}>{copy.action}</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
