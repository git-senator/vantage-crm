"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/**
 * Asks why a deal was lost, before the drop is committed.
 *
 * The server requires `lost_reason` for a losing stage and returns 409 without
 * it. Prompting at the moment of the drop turns what would be a failed drag
 * into a deliberate step, and means the answer is captured while the person
 * still remembers it.
 */
export function LostReasonDialog({
  dealTitle,
  stageName,
  onConfirm,
  onCancel,
}: {
  dealTitle: string;
  stageName: string;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onCancel();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Mark &ldquo;{dealTitle}&rdquo; as {stageName}?</DialogTitle>
          <DialogDescription>
            Recording why keeps the pipeline able to answer what is actually
            costing you deals.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="board_lost_reason">Reason</Label>
          <Textarea
            id="board_lost_reason"
            rows={3}
            autoFocus
            placeholder="Lost to a cash offer, financing fell through…"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            disabled={pending}
          />
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={pending || reason.trim() === ""}
            onClick={async () => {
              setPending(true);
              await onConfirm(reason.trim());
            }}
          >
            {pending && <Loader2 className="size-4 animate-spin" />}
            Mark as lost
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
