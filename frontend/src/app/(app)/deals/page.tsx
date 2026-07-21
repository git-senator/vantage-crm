import Link from "next/link";
import type { Metadata } from "next";
import { Handshake, Plus, Settings2, Table2 } from "lucide-react";

import { DealBoard } from "@/components/deals/deal-board";
import { PipelineSwitcher } from "@/components/deals/pipeline-switcher";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { Button } from "@/components/ui/button";
import { getDealBoard, listPipelines } from "@/lib/api/deals";
import type { DealBoard as Board } from "@/lib/api/types";
import { formatPrice, formatNumber } from "@/lib/format";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Deals" };

/**
 * Stat cards computed from the board itself.
 *
 * Every figure here is a sum over the same scoped set the columns render, so
 * the header cannot disagree with what is on screen. The prototype's invented
 * "win rate this quarter" is absent — that needs a time-bounded query against
 * stage history, which is Phase 4 reporting rather than a board header.
 */
function statsFrom(board: Board): Stat[] {
  const all = board.columns.flatMap((column) => column.deals);
  const open = all.filter((deal) => deal.status === "open");
  const won = all.filter((deal) => deal.status === "won");

  const openValue = open.reduce((sum, deal) => sum + Number(deal.value ?? 0), 0);
  const weighted = open.reduce(
    (sum, deal) => sum + Number(deal.weighted_value ?? 0),
    0,
  );
  const wonValue = won.reduce((sum, deal) => sum + Number(deal.value ?? 0), 0);

  return [
    { label: "Open deals", value: formatNumber(open.length), icon: Handshake },
    { label: "Open value", value: openValue > 0 ? formatPrice(openValue) : "—" },
    {
      label: "Weighted",
      value: weighted > 0 ? formatPrice(weighted) : "—",
      hint: "value x probability",
    },
    { label: "Won", value: wonValue > 0 ? formatPrice(wonValue) : "—" },
  ];
}

export default async function DealsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  const [pipelines, board] = await Promise.all([
    listPipelines(),
    getDealBoard({
      pipeline_id: params.pipeline_id,
      search: params.search,
    }),
  ]);

  const canManage = hasPermission(session, "deals.manage");
  const canConfigure = hasPermission(session, "settings.manage");
  const totalDeals = board.columns.reduce((sum, column) => sum + column.count, 0);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Deals"
        description="Drag a deal between stages to move it. Every move is recorded."
        actions={
          <>
            {pipelines.length > 1 && (
              <PipelineSwitcher
                pipelines={pipelines}
                current={board.pipeline_id}
              />
            )}
            {canConfigure && (
              <Button
                variant="outline"
                render={<Link href={`/deals/pipelines/${board.pipeline_id}`} />}
              >
                <Settings2 className="size-4" />
                Pipeline
              </Button>
            )}
            <Button variant="outline" render={<Link href="/deals/table" />}>
              <Table2 className="size-4" />
              Table view
            </Button>
            {canManage && (
              <Button render={<Link href="/deals/new" />}>
                <Plus className="size-4" />
                New deal
              </Button>
            )}
          </>
        }
      />

      <StatGrid stats={statsFrom(board)} />

      {totalDeals === 0 ? (
        <EmptyState
          icon={Handshake}
          title="No deals in this pipeline yet"
          description="Deals you create will appear here, and can be dragged between stages."
          action={
            canManage ? (
              <Button render={<Link href="/deals/new" />}>
                <Plus className="size-4" />
                Add your first deal
              </Button>
            ) : null
          }
        />
      ) : (
        <DealBoard board={board} canManage={canManage} />
      )}
    </div>
  );
}
