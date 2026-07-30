import Link from "next/link";
import type { Metadata } from "next";
import { Flame, Plus, Target } from "lucide-react";

import { LeadFilterBar } from "@/components/leads/lead-filters";
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
import { listLeads } from "@/lib/api/leads";
import type { LeadFilters } from "@/lib/api/types";
import { formatPrice } from "@/lib/format";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Leads" };

/** Budget as a range, or a single bound, or nothing. */
function budgetLabel(min: string | null, max: string | null): string {
  const low = min ? formatPrice(Number(min)) : null;
  const high = max ? formatPrice(Number(max)) : null;
  if (low && high) return `${low} – ${high}`;
  return low ?? high ?? "—";
}

function sourceLabel(source: string): string {
  const spaced = source.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export default async function LeadsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const session = await requireSession();
  const params = await searchParams;
  const t = await getTranslations();

  // Filters come from the URL, so a filtered view is shareable and the
  // filtering happens in the database rather than in the browser.
  const filters: LeadFilters = {
    search: params.search,
    stage: params.stage as LeadFilters["stage"],
    cursor: params.cursor,
    limit: 25,
  };

  const page = await listLeads(filters);
  const canManage = hasPermission(session, "leads.manage");
  const isFiltered = Boolean(params.search || params.stage);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.leadsTitle")}
        description={t("body.leadsDesc")}
        actions={
          canManage ? (
            <Button render={<Link href="/leads/new" />}>
              <Plus className="size-4" />
              {t("body.newLead")}
            </Button>
          ) : null
        }
      />

      <LeadFilterBar />

      {page.data.length === 0 ? (
        <EmptyState
          icon={Target}
          title={isFiltered ? t("empty.noResults") : t("empty.noLeads")}
          description={
            isFiltered ? t("empty.noResultsDesc") : t("empty.noLeadsDesc")
          }
          action={
            canManage && !isFiltered ? (
              <Button render={<Link href="/leads/new" />}>
                <Plus className="size-4" />
                {t("body.newLead")}
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
                  <TableHead className="min-w-[200px] pl-4">Lead</TableHead>
                  <TableHead>Stage</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead className="min-w-[160px]">Budget</TableHead>
                  <TableHead className="min-w-[150px]">Location</TableHead>
                  <TableHead>Owner</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.data.map((lead) => (
                  <TableRow key={lead.id} className="cursor-pointer">
                    <TableCell className="pl-4">
                      <Link href={`/leads/${lead.id}`} className="block">
                        <span className="flex items-center gap-1.5 font-medium">
                          {lead.full_name}
                          {lead.temperature === "hot" && (
                            <Flame className="size-3.5 text-destructive" />
                          )}
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          {lead.email ?? lead.phone ?? "No contact details"}
                        </span>
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={lead.stage} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {sourceLabel(lead.source)}
                    </TableCell>
                    <TableCell className="tabular whitespace-nowrap">
                      {budgetLabel(lead.budget_min, lead.budget_max)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {lead.preferred_location ?? "—"}
                    </TableCell>
                    <TableCell>
                      {lead.owner ? (
                        <div className="flex items-center gap-2">
                          <UserAvatar
                            user={{
                              id: lead.owner.id,
                              name: lead.owner.full_name,
                              initials: lead.owner.initials,
                              role: "",
                              hue: lead.owner.avatar_hue,
                            }}
                            size="xs"
                          />
                          <span className="hidden text-sm xl:inline">
                            {lead.owner.full_name.split(" ")[0]}
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
              {page.data.length === 1 ? "lead" : "leads"}
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
                      pathname: "/leads",
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
