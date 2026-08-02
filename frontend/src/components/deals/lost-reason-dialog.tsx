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
import { useTranslation } from "@/i18n/language-provider";

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
  const { t } = useTranslation();

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onCancel();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {t("body.lrdTitle", { title: dealTitle, stage: stageName })}
          </DialogTitle>
          <DialogDescription>{t("body.lrdDesc")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="board_lost_reason">{t("body.lrdReason")}</Label>
          <Textarea
            id="board_lost_reason"
            rows={3}
            autoFocus
            placeholder={t("body.lrdPlaceholder")}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            disabled={pending}
          />
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={pending}>
            {t("buttons.cancel")}
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
            {t("body.lrdConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
