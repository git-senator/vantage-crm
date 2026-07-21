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
import { deleteProperty } from "@/lib/api/properties-client";

/**
 * Delete with confirmation.
 *
 * The dialog exists because deletion is not obviously reversible from the
 * user's side — it is a soft delete server-side, but nothing in this UI
 * restores it yet, so treat it as destructive.
 *
 * A 403 here is a real outcome, not just an error: listings are shared
 * inventory, so an agent can open a colleague's listing and reach this button.
 * The server's message explains that, and it is surfaced verbatim.
 */
export function DeletePropertyButton({
  propertyId,
  propertyTitle,
}: {
  propertyId: string;
  propertyTitle: string;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  async function handleDelete() {
    setPending(true);
    setError(null);
    try {
      await deleteProperty(propertyId);
      setOpen(false);
      router.push("/properties");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Unable to delete. Please try again.",
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
            Delete
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete this listing?</DialogTitle>
          <DialogDescription>
            {propertyTitle} will be removed from your inventory. This cannot be
            undone from here.
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
            render={<Button variant="ghost" disabled={pending}>Cancel</Button>}
          />
          <Button variant="destructive" onClick={handleDelete} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            Delete listing
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
