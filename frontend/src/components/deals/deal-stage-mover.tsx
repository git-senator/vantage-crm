"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight, Loader2, TriangleAlert } from "lucide-react";

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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ClientApiError } from "@/lib/api/client";
import { moveDealStage } from "@/lib/api/deals-client";
import type { Deal, PipelineStage } from "@/lib/api/types";

/**
 * Move a deal to another stage.
 *
 * A dialog rather than an inline dropdown, because this is not a field edit:
 * the server writes stage history, resets probability and sets or clears the
 * close date. The dialog makes that weight visible, and gives the losing path
 * somewhere to ask for a reason.
 *
 * The reason field appears only for a losing stage, and the submit button is
 * disabled without it — the server enforces this too, but failing at the point
 * of intent is better than failing on submit.
 */
export function DealStageMover({
  deal,
  stages,
}: {
  deal: Deal;
  stages: PipelineStage[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const others = stages.filter((stage) => stage.id !== deal.stage.id);
  const [target, setTarget] = useState(others[0]?.id ?? "");
  const [note, setNote] = useState("");
  const [lostReason, setLostReason] = useState("");

  const targetStage = stages.find((stage) => stage.id === target);
  const needsReason = targetStage?.is_lost ?? false;
  const blocked = needsReason && lostReason.trim() === "";

  async function handleMove() {
    setPending(true);
    setError(null);
    try {
      await moveDealStage(deal.id, {
        to_stage_id: target,
        note: note.trim() || null,
        lost_reason: needsReason ? lostReason.trim() : null,
      });
      setOpen(false);
      setNote("");
      setLostReason("");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Unable to move the deal. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  if (others.length === 0) return null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline">
            <ArrowRight className="size-4" />
            Move stage
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move this deal</DialogTitle>
          <DialogDescription>
            Currently in {deal.stage.name}. Moving records the change in the
            deal&apos;s history and updates its probability.
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

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="to_stage">Move to</Label>
            <Select
              value={target}
              onValueChange={(next) => setTarget(next ?? "")}
              disabled={pending}
            >
              <SelectTrigger id="to_stage" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {others.map((stage) => (
                  <SelectItem key={stage.id} value={stage.id}>
                    {stage.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {needsReason && (
            <div className="space-y-2">
              <Label htmlFor="lost_reason">Why was it lost?</Label>
              <Textarea
                id="lost_reason"
                rows={3}
                placeholder="Financing fell through, lost to a cash offer…"
                value={lostReason}
                onChange={(event) => setLostReason(event.target.value)}
                disabled={pending}
              />
              <p className="text-xs text-muted-foreground">
                Required. This is the data behind &quot;why do we lose deals&quot;.
              </p>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="note">Note</Label>
            <Textarea
              id="note"
              rows={2}
              placeholder="Optional — added to the deal's timeline"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              disabled={pending}
            />
          </div>
        </div>

        <DialogFooter>
          <DialogClose
            render={<Button variant="ghost" disabled={pending}>Cancel</Button>}
          />
          <Button onClick={handleMove} disabled={pending || blocked || !target}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            Move deal
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
