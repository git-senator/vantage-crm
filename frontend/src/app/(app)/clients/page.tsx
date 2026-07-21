import Link from "next/link";
import type { Metadata } from "next";
import {
  Building2,
  Handshake,
  Mail,
  Phone,
  Plus,
  Target,
  Users,
} from "lucide-react";

import { ClientFilterBar } from "@/components/clients/client-filters";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getTypeCounts, listClients } from "@/lib/api/clients";
import type { Client, ClientFilters } from "@/lib/api/types";
import { formatNumber, formatPrice, titleize } from "@/lib/format";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Clients" };

/**
 * Avatar hue from the client id.
 *
 * Deterministic, so a client keeps the same colour across renders and pages.
 * The prototype cycled an array by list index, which meant the colour changed
 * as soon as the list was filtered or paged.
 */
function hueFor(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return hash;
}

function initialsFor(name: string): string {
  return (
    name
      .replace(/&/g, "")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase() || "?"
  );
}

/** Clients aren't team members, but UserAvatar takes the same shape. */
function asAvatarSubject(client: Client) {
  return {
    id: client.id,
    name: client.display_name,
    initials: initialsFor(client.display_name),
    role: titleize(client.type),
    hue: hueFor(client.id),
  };
}

/**
 * Stat cards, from real counts only.
 *
 * The prototype showed "Lifetime volume" and "Repeat client rate". Neither has
 * a data source until Deals ships, and a fabricated figure on a dashboard is
 * worse than an absent one — so they are omitted rather than mocked.
 */
function statsFrom(counts: Record<string, number>): Stat[] {
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  return [
    { label: "Clients", value: formatNumber(total), icon: Users },
    { label: "Buyers", value: formatNumber(counts.buyer ?? 0), icon: Target },
    {
      label: "Sellers",
      value: formatNumber(counts.seller ?? 0),
      icon: Building2,
    },
    {
      label: "Investors",
      value: formatNumber(counts.investor ?? 0),
      icon: Handshake,
    },
  ];
}

export default async function ClientsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  // Filters come from the URL, so a filtered view is shareable and the
  // filtering happens in the database rather than in the browser.
  const filters: ClientFilters = {
    search: params.search,
    type: params.type as ClientFilters["type"],
    status: params.status as ClientFilters["status"],
    cursor: params.cursor,
    limit: 24,
  };

  // Both are scoped to the caller server-side, so the counts can never
  // describe records the list does not contain.
  const [page, counts] = await Promise.all([
    listClients(filters),
    getTypeCounts(),
  ]);

  const canManage = hasPermission(session, "contacts.manage");
  const isFiltered = Boolean(params.search || params.type || params.status);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Clients"
        description="Everyone you actively represent, plus the relationships worth reviving."
        actions={
          canManage ? (
            <Button render={<Link href="/clients/new" />}>
              <Plus className="size-4" />
              Add client
            </Button>
          ) : null
        }
      />

      <StatGrid stats={statsFrom(counts)} />

      <ClientFilterBar counts={counts} />

      {page.data.length === 0 ? (
        <EmptyState
          icon={Users}
          title={isFiltered ? "No clients match those filters" : "No clients yet"}
          description={
            isFiltered
              ? "Try a different search term or clear the filters."
              : "Clients you add, or convert from a lead, will appear here."
          }
          action={
            canManage && !isFiltered ? (
              <Button render={<Link href="/clients/new" />}>
                <Plus className="size-4" />
                Add your first client
              </Button>
            ) : null
          }
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {page.data.map((client) => (
              <Card
                key={client.id}
                className="gap-0 py-0 transition-shadow hover:shadow-md"
              >
                <CardContent className="p-5">
                  <div className="flex items-start gap-3">
                    <UserAvatar user={asAvatarSubject(client)} size="lg" />
                    <div className="min-w-0 flex-1">
                      <Link
                        href={`/clients/${client.id}`}
                        className="block truncate font-medium hover:underline"
                      >
                        {client.display_name}
                      </Link>
                      <p className="truncate text-sm text-muted-foreground">
                        {client.email ?? client.phone ?? "No contact details"}
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <StatusBadge status={client.status} />
                        <StatusBadge
                          status={client.type}
                          label={titleize(client.type)}
                          tone="neutral"
                          dot={false}
                        />
                      </div>
                    </div>
                  </div>

                  <dl className="mt-5 grid grid-cols-2 gap-2 rounded-lg bg-muted/50 p-3 text-center">
                    <div>
                      <dt className="text-[11px] text-muted-foreground">
                        Lifetime
                      </dt>
                      <dd className="tabular mt-0.5 text-sm font-semibold">
                        {client.lifetime_value
                          ? formatPrice(Number(client.lifetime_value))
                          : "—"}
                      </dd>
                    </div>
                    <div className="border-l">
                      <dt className="text-[11px] text-muted-foreground">
                        Client since
                      </dt>
                      <dd className="mt-0.5 text-sm font-semibold">
                        {client.client_since ?? "—"}
                      </dd>
                    </div>
                  </dl>
                </CardContent>

                <Separator />

                <CardFooter className="flex items-center justify-between gap-2 px-5 py-3">
                  <div className="flex min-w-0 items-center gap-2">
                    {client.owner ? (
                      <>
                        <UserAvatar
                          user={{
                            id: client.owner.id,
                            name: client.owner.full_name,
                            initials: client.owner.initials,
                            role: "",
                            hue: client.owner.avatar_hue,
                          }}
                          size="xs"
                        />
                        <span className="truncate text-xs text-muted-foreground">
                          {client.owner.full_name}
                        </span>
                      </>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        Unassigned
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {client.phone && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Call ${client.display_name}`}
                        render={<a href={`tel:${client.phone}`} />}
                      >
                        <Phone className="size-4" />
                      </Button>
                    )}
                    {client.email && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Email ${client.display_name}`}
                        render={<a href={`mailto:${client.email}`} />}
                      >
                        <Mail className="size-4" />
                      </Button>
                    )}
                  </div>
                </CardFooter>
              </Card>
            ))}
          </div>

          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              Showing{" "}
              <span className="font-medium text-foreground">
                {page.data.length}
              </span>{" "}
              {page.data.length === 1 ? "client" : "clients"}
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
                      pathname: "/clients",
                      query: { ...params, cursor: page.meta.next_cursor },
                    }}
                  />
                }
              >
                Next page
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
