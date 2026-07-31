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
import { getTranslations } from "@/i18n/server";
import type { TranslateFn } from "@/i18n/translate";
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
function statsFrom(board: Board, t: TranslateFn): Stat[] {
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
    { label: t("body.dealsOpenDeals"), value: formatNumber(open.length), icon: Handshake },
    { label: t("body.dealsOpenValue"), value: openValue > 0 ? formatPrice(openValue) : "—" },
    {
      label: t("body.dealsWeighted"),
      value: weighted > 0 ? formatPrice(weighted) : "—",
      hint: t("body.dealsWeightedHint"),
    },
    { label: t("body.dealsWon"), value: wonValue > 0 ? formatPrice(wonValue) : "—" },
  ];
}

export default async function DealsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const session = await requireSession();
  const params = await searchParams;
  const t = await getTranslations();

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
        title={t("body.dealsTitle")}
        description={t("body.dealsDesc")}
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
                {t("pipelines.title")}
              </Button>
            )}
            <Button variant="outline" render={<Link href="/deals/table" />}>
              <Table2 className="size-4" />
              {t("deals.table")}
            </Button>
            {canManage && (
              <Button render={<Link href="/deals/new" />}>
                <Plus className="size-4" />
                {t("body.newDeal")}
              </Button>
            )}
          </>
        }
      />

      <StatGrid stats={statsFrom(board, t)} />

      {totalDeals === 0 ? (
        <EmptyState
          icon={Handshake}
          title={t("body.dealsEmptyTitle")}
          description={t("body.dealsEmptyDesc")}
          action={
            canManage ? (
              <Button render={<Link href="/deals/new" />}>
                <Plus className="size-4" />
                {t("body.newDeal")}
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
