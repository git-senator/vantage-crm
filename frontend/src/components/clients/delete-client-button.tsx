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
import { deleteClient } from "@/lib/api/clients-client";

/**
 * Delete with confirmation.
 *
 * The dialog exists because deletion is not obviously reversible from the
 * user's side — it is a soft delete server-side, but nothing in this UI
 * restores it yet, so treat it as destructive.
 */
export function DeleteClientButton({
  clientId,
  clientName,
}: {
  clientId: string;
  clientName: string;
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
      await deleteClient(clientId);
      setOpen(false);
      router.push("/clients");
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
          <DialogTitle>{t("body.delClient")}</DialogTitle>
          <DialogDescription>
            {t("body.delClientBody", { name: clientName })}
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
            {t("body.delClientConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
