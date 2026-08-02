"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2, Trash2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ClientApiError } from "@/lib/api/client";
import { useTranslation } from "@/i18n/language-provider";
import { deleteDeal } from "@/lib/api/deals-client";

/**
 * Delete with confirmation.
 *
 * Soft delete server-side, and the stage history survives it — so the deal
 * leaves the pipeline but the cycle-time analytics it contributed to stay
 * intact. Nothing in this UI restores it, so treat it as destructive.
 */
export function DeleteDealButton({
  dealId,
  dealTitle,
}: {
  dealId: string;
  dealTitle: string;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  async function handleDelete() {
    setPending(true);
    setError(null);
    try {
      await deleteDeal(dealId);
      setOpen(false);
      router.push("/deals/table");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : t("body.taskDeleteError"),
      );
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" className="text-destructive">
            <Trash2 className="size-4" />
            {t("buttons.delete")}
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("body.delDeal")}</DialogTitle>
          <DialogDescription>
            {t("body.delDealBody", { name: dealTitle })}
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <DialogFooter>
          <DialogClose
            render={
              <Button variant="ghost" disabled={pending}>
                {t("buttons.cancel")}
              </Button>
            }
          />
          <Button variant="destructive" onClick={handleDelete} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {t("body.delDealConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
