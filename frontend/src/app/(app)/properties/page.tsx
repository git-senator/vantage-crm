import Link from "next/link";
import type { Metadata } from "next";
import { Bath, Bed, Building2, CheckCircle2, Clock, Plus, Ruler, Tag } from "lucide-react";

import { PropertyFilterBar } from "@/components/properties/property-filters";
import { PropertyThumb } from "@/components/properties/property-thumb";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getStatusCounts, listProperties } from "@/lib/api/properties";
import type { PropertyFilters } from "@/lib/api/types";
import { formatArea, formatNumber, titleize } from "@/lib/format";
import { listingPrice } from "@/lib/listing";
import { getTranslations } from "@/i18n/server";
import type { TranslateFn } from "@/i18n/translate";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Properties" };

/**
 * Stat cards, from real counts only.
 *
 * The prototype showed "Total list value", "Avg. days on market" and "Listing
 * views" with invented figures. None has a source yet — value needs a SUM
 * endpoint, and view tracking is a later phase — so they are omitted rather
 * than mocked. A fabricated number on a dashboard is worse than an absent one.
 */
function statsFrom(counts: Record<string, number>, t: TranslateFn): Stat[] {
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  return [
    { label: t("body.propListings"), value: formatNumber(total), icon: Building2 },
    { label: t("body.propActive"), value: formatNumber(counts.active ?? 0), icon: Tag },
    { label: t("body.propPending"), value: formatNumber(counts.pending ?? 0), icon: Clock },
    {
      label: t("body.propSold"),
      value: formatNumber(counts.sold ?? 0),
      icon: CheckCircle2,
    },
  ];
}

export default async function PropertiesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const session = await requireSession();
  const params = await searchParams;
  const t = await getTranslations();
  const units = session.organization.measurement_system;

  // Filters come from the URL, so a filtered view is shareable and the
  // filtering happens in the database rather than in the browser.
  const filters: PropertyFilters = {
    search: params.search,
    status: params.status as PropertyFilters["status"],
    property_type: params.property_type as PropertyFilters["property_type"],
    min_price: params.min_price,
    max_price: params.max_price,
    cursor: params.cursor,
    limit: 24,
  };

  const [page, counts] = await Promise.all([
    listProperties(filters),
    getStatusCounts(),
  ]);

  const canManage = hasPermission(session, "properties.manage");
  const isFiltered = Boolean(
    params.search || params.status || params.property_type || params.min_price,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.propertiesTitle")}
        description={t("body.propertiesDesc")}
        actions={
          canManage ? (
            <Button render={<Link href="/properties/new" />}>
              <Plus className="size-4" />
              {t("body.newListing")}
            </Button>
          ) : null
        }
      />

      <StatGrid stats={statsFrom(counts, t)} />

      <PropertyFilterBar counts={counts} />

      {page.data.length === 0 ? (
        <EmptyState
          icon={Building2}
          title={isFiltered ? t("empty.noResults") : t("empty.noProperties")}
          description={
            isFiltered ? t("empty.noResultsDesc") : t("empty.noPropertiesDesc")
          }
          action={
            canManage && !isFiltered ? (
              <Button render={<Link href="/properties/new" />}>
                <Plus className="size-4" />
                {t("body.newListing")}
              </Button>
            ) : null
          }
        />
      ) : (
        <>
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {page.data.map((property) => (
              <Card
                key={property.id}
                className="gap-0 overflow-hidden py-0 transition-shadow hover:shadow-lg"
              >
                <Link href={`/properties/${property.id}`} className="block">
                  <PropertyThumb
                    property={property}
                    priceLabel={listingPrice(property, t)}
                  />
                </Link>

                <CardContent className="p-4">
                  <Link
                    href={`/properties/${property.id}`}
                    className="block truncate font-medium hover:underline"
                  >
                    {property.title}
                  </Link>
                  <p className="mt-0.5 truncate text-sm text-muted-foreground">
                    {property.full_address}
                  </p>

                  <div className="tabular mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
                    {property.bedrooms ? (
                      <span className="flex items-center gap-1.5">
                        <Bed className="size-4" />
                        {property.bedrooms}
                      </span>
                    ) : null}
                    {property.bathrooms ? (
                      <span className="flex items-center gap-1.5">
                        <Bath className="size-4" />
                        {Number(property.bathrooms)}
                      </span>
                    ) : null}
                    {property.square_feet ? (
                      <span className="flex items-center gap-1.5">
                        <Ruler className="size-4" />
                        {formatArea(property.square_feet, units)}
                      </span>
                    ) : null}
                  </div>

                  <div className="mt-3 flex items-center gap-1.5">
                    <StatusBadge
                      status={property.property_type}
                      label={titleize(property.property_type)}
                      tone="neutral"
                      dot={false}
                    />
                    {property.listing_kind === "rent" && (
                      <StatusBadge
                        status="rent"
                        label={t("body.dealRent")}
                        tone="neutral"
                        dot={false}
                      />
                    )}
                    {property.days_on_market !== null && (
                      <span className="text-xs text-muted-foreground">
                        {t("body.propDaysOnMarket", { n: property.days_on_market })}
                      </span>
                    )}
                  </div>
                </CardContent>

                <Separator />

                <CardFooter className="flex items-center justify-between gap-2 px-4 py-3">
                  {property.listing_agent ? (
                    <div className="flex min-w-0 items-center gap-2">
                      <UserAvatar
                        user={{
                          id: property.listing_agent.id,
                          name: property.listing_agent.full_name,
                          initials: property.listing_agent.initials,
                          role: "",
                          hue: property.listing_agent.avatar_hue,
                        }}
                        size="xs"
                      />
                      <span className="truncate text-xs text-muted-foreground">
                        {property.listing_agent.full_name}
                      </span>
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {t("body.unassigned")}
                    </span>
                  )}
                  {property.mls_number && (
                    <span className="tabular shrink-0 text-xs text-muted-foreground">
                      {property.mls_number}
                    </span>
                  )}
                </CardFooter>
              </Card>
            ))}
          </div>

          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {t("body.propShowing", { n: page.data.length })}
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
                      pathname: "/properties",
                      query: { ...params, cursor: page.meta.next_cursor },
                    }}
                  />
                }
              >
                {t("body.nextPage")}
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
