"use client";

import { useRouter } from "next/navigation";
import { useMemo, useOptimistic, useState, useTransition } from "react";
import Link from "next/link";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { GripVertical, Loader2, TriangleAlert } from "lucide-react";

import { LostReasonDialog } from "@/components/deals/lost-reason-dialog";
import { Card } from "@/components/ui/card";
import { ClientApiError } from "@/lib/api/client";
import { moveDealStage } from "@/lib/api/deals-client";
import type { Deal, DealBoard as Board } from "@/lib/api/types";
import { formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Accent colour per column, keyed to the stage's outcome rather than its
 * index — the prototype hard-coded six colours in pipeline order, which breaks
 * the moment a brokerage reorders or adds a stage.
 */
function accentFor(stage: Board["columns"][number]["stage"]): string {
  if (stage.is_won) return "bg-success";
  if (stage.is_lost) return "bg-destructive";
  const palette = [
    "bg-muted-foreground/40",
    "bg-info",
    "bg-warning",
    "bg-chart-4",
    "bg-primary",
  ];
  return palette[stage.position % palette.length];
}

export function DealBoard({
  board,
  canManage,
}: {
  board: Board;
  canManage: boolean;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState<Deal | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  //: Set when a drop lands on a losing stage and needs a reason first.
  const [lostPrompt, setLostPrompt] = useState<{
    deal: Deal;
    stageId: string;
    stageName: string;
  } | null>(null);

  // The board renders from this, so a card moves the instant it is dropped and
  // reverts if the server refuses. Without it every drag would wait a round
  // trip before the card appeared in its new column.
  const [optimistic, applyOptimistic] = useOptimistic(
    board,
    (current: Board, move: { dealId: string; toStageId: string }) => {
      const moving = current.columns
        .flatMap((column) => column.deals)
        .find((deal) => deal.id === move.dealId);
      if (!moving) return current;

      return {
        ...current,
        columns: current.columns.map((column) => {
          const without = column.deals.filter((deal) => deal.id !== move.dealId);
          const deals =
            column.stage.id === move.toStageId ? [moving, ...without] : without;
          return {
            ...column,
            deals,
            count: deals.length,
            total_value: String(
              deals.reduce((sum, deal) => sum + Number(deal.value ?? 0), 0),
            ),
          };
        }),
      };
    },
  );

  const sensors = useSensors(
    // A small distance threshold so a click on a card still navigates rather
    // than being swallowed as a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    // Keyboard support is why this is @dnd-kit and not native HTML5 drag
    // events: a deal can be moved with the keyboard alone.
    useSensor(KeyboardSensor),
  );

  const dealsById = useMemo(() => {
    const map = new Map<string, Deal>();
    for (const column of board.columns) {
      for (const deal of column.deals) map.set(deal.id, deal);
    }
    return map;
  }, [board]);

  function handleDragStart(event: DragStartEvent) {
    setDragging(dealsById.get(String(event.active.id)) ?? null);
  }

  async function commitMove(
    deal: Deal,
    toStageId: string,
    lostReason?: string,
  ) {
    setPendingId(deal.id);
    setError(null);
    startTransition(() => {
      applyOptimistic({ dealId: deal.id, toStageId });
    });

    try {
      await moveDealStage(deal.id, {
        to_stage_id: toStageId,
        lost_reason: lostReason ?? null,
      });
      // Re-read from the server: the transition also changed probability and
      // possibly the close date, which the optimistic patch does not model.
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Could not move the deal. It has been put back.",
      );
      // useOptimistic discards its patch once the transition settles, so the
      // card returns to its original column on its own.
      router.refresh();
    } finally {
      setPendingId(null);
    }
  }

  function handleDragEnd(event: DragEndEvent) {
    setDragging(null);
    const { active, over } = event;
    if (!over) return;

    const deal = dealsById.get(String(active.id));
    const toStageId = String(over.id);
    if (!deal || deal.stage.id === toStageId) return;

    const column = board.columns.find((entry) => entry.stage.id === toStageId);
    if (!column) return;

    // The server requires a reason for a losing stage and would reject this
    // with a 409. Asking first turns a failed drop into a deliberate step.
    if (column.stage.is_lost) {
      setLostPrompt({
        deal,
        stageId: toStageId,
        stageName: column.stage.name,
      });
      return;
    }

    void commitMove(deal, toStageId);
  }

  return (
    <div className="space-y-4">
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        {/* Horizontal scroll keeps every column reachable on a laptop. */}
        <div className="scrollbar-slim -mx-4 overflow-x-auto px-4 pb-4 md:-mx-6 md:px-6">
          <div className="flex min-w-max gap-4">
            {optimistic.columns.map((column) => (
              <BoardColumn
                key={column.stage.id}
                column={column}
                canManage={canManage}
                pendingId={pendingId}
              />
            ))}
          </div>
        </div>

        <DragOverlay>
          {dragging ? <DealCard deal={dragging} overlay /> : null}
        </DragOverlay>
      </DndContext>

      {lostPrompt && (
        <LostReasonDialog
          dealTitle={lostPrompt.deal.title}
          stageName={lostPrompt.stageName}
          onCancel={() => setLostPrompt(null)}
          onConfirm={async (reason) => {
            const prompt = lostPrompt;
            setLostPrompt(null);
            await commitMove(prompt.deal, prompt.stageId, reason);
          }}
        />
      )}
    </div>
  );
}

function BoardColumn({
  column,
  canManage,
  pendingId,
}: {
  column: Board["columns"][number];
  canManage: boolean;
  pendingId: string | null;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: column.stage.id });

  return (
    <section
      ref={setNodeRef}
      className={cn(
        "flex w-[300px] shrink-0 flex-col rounded-xl bg-muted/40 transition-colors",
        isOver && "bg-accent ring-2 ring-primary/40",
      )}
      aria-label={column.stage.name}
    >
      <header className="flex items-center gap-2 px-3 py-3">
        <span className={cn("size-2 rounded-full", accentFor(column.stage))} />
        <h2 className="text-sm font-medium">{column.stage.name}</h2>
        <span className="tabular rounded-full bg-background px-1.5 text-xs text-muted-foreground">
          {column.count}
        </span>
        <span className="tabular ml-auto text-xs font-medium text-muted-foreground">
          {Number(column.total_value) > 0
            ? formatPrice(Number(column.total_value))
            : "—"}
        </span>
      </header>

      <div className="flex flex-1 flex-col gap-2.5 px-2.5 pb-2.5">
        {column.deals.map((deal) => (
          <DealCard
            key={deal.id}
            deal={deal}
            draggable={canManage}
            pending={pendingId === deal.id}
          />
        ))}
        {column.deals.length === 0 && (
          <p className="px-1 py-6 text-center text-xs text-muted-foreground">
            Nothing here
          </p>
        )}
      </div>
    </section>
  );
}

function DealCard({
  deal,
  draggable = false,
  overlay = false,
  pending = false,
}: {
  deal: Deal;
  draggable?: boolean;
  overlay?: boolean;
  pending?: boolean;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: deal.id,
    disabled: !draggable,
  });

  return (
    <Card
      ref={overlay ? undefined : setNodeRef}
      className={cn(
        "group gap-0 p-3.5 transition-shadow hover:shadow-md",
        draggable && "cursor-grab active:cursor-grabbing",
        isDragging && !overlay && "opacity-40",
        overlay && "shadow-lg",
        pending && "opacity-60",
      )}
      {...(draggable && !overlay ? attributes : {})}
      {...(draggable && !overlay ? listeners : {})}
    >
      <div className="flex items-start gap-2">
        <Link
          href={`/deals/${deal.id}`}
          className="min-w-0 flex-1 text-sm leading-snug font-medium hover:underline"
          // Stops a click reaching the drag listeners on the card.
          onPointerDown={(event) => event.stopPropagation()}
        >
          {deal.title}
        </Link>
        {pending ? (
          <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
        ) : (
          draggable && (
            <GripVertical className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
          )
        )}
      </div>

      <p className="mt-1.5 truncate text-xs text-muted-foreground">
        {deal.client.display_name}
      </p>

      <div className="mt-3 flex items-baseline justify-between">
        <span className="tabular text-base font-semibold">
          {deal.value ? formatPrice(Number(deal.value)) : "—"}
        </span>
        <span className="tabular text-xs text-muted-foreground">
          {deal.commission_amount
            ? `${formatPrice(Number(deal.commission_amount))} comm.`
            : `${deal.probability}%`}
        </span>
      </div>
    </Card>
  );
}
