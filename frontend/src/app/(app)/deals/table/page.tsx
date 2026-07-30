import Link from "next/link";
import type { Metadata } from "next";
import { Handshake, LayoutGrid, Plus } from "lucide-react";

import { DealFilterBar } from "@/components/deals/deal-filters";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listDeals } from "@/lib/api/deals";
import type { DealFilters } from "@/lib/api/types";
import { formatPrice } from "@/lib/format";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Deals" };

export default async function DealsTablePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const session = await requireSession();
  const params = await searchParams;
  const t = await getTranslations();

  const filters: DealFilters = {
    search: params.search,
    status: params.status as DealFilters["status"],
    priority: params.priority as DealFilters["priority"],
    cursor: params.cursor,
    limit: 25,
  };

  const page = await listDeals(filters);
  const canManage = hasPermission(session, "deals.manage");
  const isFiltered = Boolean(params.search || params.status || params.priority);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.dealsTitle")}
        description={t("body.dealsTableDesc")}
        actions={
          <>
            <Button variant="outline" render={<Link href="/deals" />}>
              <LayoutGrid className="size-4" />
              {t("deals.board")}
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

      <DealFilterBar />

      {page.data.length === 0 ? (
        <EmptyState
          icon={Handshake}
          title={isFiltered ? "No deals match those filters" : "No deals yet"}
          description={
            isFiltered
              ? "Try a different search term or clear the filters."
              : "Deals you create will appear here and on the board."
          }
          action={
            canManage && !isFiltered ? (
              <Button render={<Link href="/deals/new" />}>
                <Plus className="size-4" />
                Add your first deal
              </Button>
            ) : null
          }
        />
      ) : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="min-w-[240px] pl-4">Deal</TableHead>
                  <TableHead>Stage</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="min-w-[130px]">Value</TableHead>
                  <TableHead className="min-w-[120px]">Commission</TableHead>
                  <TableHead>Close</TableHead>
                  <TableHead>Owner</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.data.map((deal) => (
                  <TableRow key={deal.id} className="cursor-pointer">
                    <TableCell className="pl-4">
                      <Link href={`/deals/${deal.id}`} className="block">
                        <span className="font-medium">{deal.title}</span>
                        <span className="block text-xs text-muted-foreground">
                          {deal.client.display_name}
                        </span>
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        status={deal.stage.key}
                        label={deal.stage.name}
                        tone="neutral"
                        dot={false}
                      />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={deal.status} />
                    </TableCell>
                    <TableCell className="tabular whitespace-nowrap">
                      {deal.value ? formatPrice(Number(deal.value)) : "—"}
                    </TableCell>
                    <TableCell className="tabular whitespace-nowrap text-muted-foreground">
                      {deal.commission_amount
                        ? formatPrice(Number(deal.commission_amount))
                        : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {deal.actual_close_date ?? deal.expected_close_date ?? "—"}
                    </TableCell>
                    <TableCell>
                      {deal.owner ? (
                        <div className="flex items-center gap-2">
                          <UserAvatar
                            user={{
                              id: deal.owner.id,
                              name: deal.owner.full_name,
                              initials: deal.owner.initials,
                              role: "",
                              hue: deal.owner.avatar_hue,
                            }}
                            size="xs"
                          />
                          <span className="hidden text-sm xl:inline">
                            {deal.owner.full_name.split(" ")[0]}
                          </span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground">Unassigned</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between gap-3 border-t px-4 py-3">
            <p className="text-sm text-muted-foreground">
              Showing{" "}
              <span className="font-medium text-foreground">
                {page.data.length}
              </span>{" "}
              {page.data.length === 1 ? "deal" : "deals"}
            </p>
            {/* Keyset pagination: the cursor is the last row seen, so a row
                inserted mid-paging cannot shift the window. */}
            {page.meta.has_more && page.meta.next_cursor && (
              <Button
                variant="outline"
                size="sm"
                render={
                  <Link
                    href={{
                      pathname: "/deals/table",
                      query: { ...params, cursor: page.meta.next_cursor },
                    }}
                  />
                }
              >
                Next page
              </Button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
