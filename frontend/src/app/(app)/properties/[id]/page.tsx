import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ArrowLeft, Bath, Bed, MapPin, Pencil, Ruler, User } from "lucide-react";

import { DeletePropertyButton } from "@/components/properties/delete-property-button";
import { PropertyGallery } from "@/components/properties/property-gallery";
import { PropertyIntelligence } from "@/components/properties/property-intelligence";
import { PropertyThumb } from "@/components/properties/property-thumb";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getProperty, getPropertyPhotos } from "@/lib/api/properties";
import { ApiError } from "@/lib/api/server";
import { RecordActivity } from "@/components/shared/record-activity";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";
import { formatArea } from "@/lib/format";
import { listingPrice } from "@/lib/listing";

export const metadata: Metadata = { title: "Property" };

export default async function PropertyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const t = await getTranslations();
  const units = session.organization.measurement_system;

  let property;
  try {
    property = await getProperty(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  // Photography is decoration, not the record: a storage hiccup must leave the
  // listing readable rather than turning the whole page into an error.
  let photos: Awaited<ReturnType<typeof getPropertyPhotos>> = [];
  try {
    photos = await getPropertyPhotos(id);
  } catch {
    photos = [];
  }

  // Listings are shared inventory: every agent can open this page, but only
  // the listing agent may change it. Hiding the buttons for everyone else is
  // UX — the API returns 403 regardless of what this renders.
  const canManage = hasPermission(session, "properties.manage");
  const isMine = property.listing_agent?.id === session.id;
  const canEdit =
    canManage && (isMine || hasPermission(session, "properties.assign"));

  return (
    <div className="space-y-6">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 text-muted-foreground"
        render={<Link href="/properties" />}
      >
        <ArrowLeft className="size-4" />
        {t("body.dvBackProperties")}
      </Button>

      <PageHeader
        title={property.title}
        description={property.full_address}
        actions={
          canEdit ? (
            <>
              <Button
                variant="outline"
                render={<Link href={`/properties/${property.id}/edit`} />}
              >
                <Pencil className="size-4" />
                {t("buttons.edit")}
              </Button>
              <DeletePropertyButton
                propertyId={property.id}
                propertyTitle={property.title}
              />
            </>
          ) : null
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0 space-y-6">
          {photos.length > 0 ? (
            <PropertyGallery photos={photos} title={property.title} />
          ) : (
            <Card className="overflow-hidden p-0">
              <PropertyThumb
                property={property}
                className="relative aspect-[21/9] overflow-hidden"
              />
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>{t("body.dvDetails")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <Detail label={t("forms.price")}>
                <span className="tabular font-medium">
                  {listingPrice(property, t)}
                </span>
              </Detail>
              <Detail label={t("forms.listingKind")}>
                <StatusBadge
                  status={property.listing_kind}
                  label={t(
                    property.listing_kind === "rent"
                      ? "body.dealRent"
                      : "body.dealSale",
                  )}
                  tone="neutral"
                  dot={false}
                />
              </Detail>
              <Detail label={t("forms.status")}>
                <StatusBadge
                  status={property.status}
                  label={t(`body.propStatus_${property.status}`)}
                />
              </Detail>
              <Detail label={t("forms.type")}>
                <StatusBadge
                  status={property.property_type}
                  label={t(`body.propType_${property.property_type}`)}
                  tone="neutral"
                  dot={false}
                />
              </Detail>
              {property.mls_number && (
                <Detail label={t("body.pfMls")}>
                  <span className="tabular">{property.mls_number}</span>
                </Detail>
              )}
              {property.year_built && (
                <Detail label={t("body.pfYearBuilt")}>
                  <span className="tabular">{property.year_built}</span>
                </Detail>
              )}
              {property.lot_size_sqft && (
                <Detail label={t("body.dvLotSize")}>
                  <span className="tabular">
                    {formatArea(property.lot_size_sqft, units)}
                  </span>
                </Detail>
              )}
              {property.listed_at && (
                <Detail label={t("body.pfListedOn")}>{property.listed_at}</Detail>
              )}
              {property.days_on_market !== null && (
                <Detail label={t("body.dvDaysOnMarketLabel")}>
                  <span className="tabular">{property.days_on_market}</span>
                </Detail>
              )}
            </CardContent>
          </Card>

          {property.description && (
            <Card>
              <CardHeader>
                <CardTitle>{t("body.pfDescription")}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">
                  {property.description}
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <PropertyIntelligence propertyId={property.id} />

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("body.dvAtAGlance")}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="tabular flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
                {property.bedrooms ? (
                  <span className="flex items-center gap-1.5">
                    <Bed className="size-4 text-muted-foreground" />
                    {t("body.dvBd", { n: property.bedrooms })}
                  </span>
                ) : null}
                {property.bathrooms ? (
                  <span className="flex items-center gap-1.5">
                    <Bath className="size-4 text-muted-foreground" />
                    {t("body.dvBa", { n: Number(property.bathrooms) })}
                  </span>
                ) : null}
                {property.square_feet ? (
                  <span className="flex items-center gap-1.5">
                    <Ruler className="size-4 text-muted-foreground" />
                    {formatArea(property.square_feet, units)}
                  </span>
                ) : null}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("body.pfAddress")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <p className="flex items-start gap-2 text-muted-foreground">
                <MapPin className="mt-0.5 size-4 shrink-0" />
                <span>{property.full_address}</span>
              </p>
              {property.latitude && property.longitude && (
                <p className="tabular text-xs text-muted-foreground">
                  {Number(property.latitude).toFixed(5)},{" "}
                  {Number(property.longitude).toFixed(5)}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">
                {t("body.dvListingAgent")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {property.listing_agent ? (
                <div className="flex items-center gap-3">
                  <UserAvatar
                    user={{
                      id: property.listing_agent.id,
                      name: property.listing_agent.full_name,
                      initials: property.listing_agent.initials,
                      role: "",
                      hue: property.listing_agent.avatar_hue,
                    }}
                    size="md"
                  />
                  <span className="text-sm font-medium">
                    {property.listing_agent.full_name}
                  </span>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {t("body.unassigned")}
                </p>
              )}
              {canManage && !canEdit && (
                <p className="mt-3 text-xs text-muted-foreground">
                  {t("body.dvNotYourListing")}
                </p>
              )}
            </CardContent>
          </Card>

          {property.client_id && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t("body.dvSeller")}</CardTitle>
              </CardHeader>
              <CardContent>
                <Link
                  href={`/clients/${property.client_id}`}
                  className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
                >
                  <User className="size-4 shrink-0" />
                  {t("body.dvViewClientRecord")}
                </Link>
              </CardContent>
            </Card>
          )}

          {property.features.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t("body.pfFeatures")}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {property.features.map((feature) => (
                    <span
                      key={feature}
                      className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground"
                    >
                      {feature}
                    </span>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <RecordActivity
        entityType="property"
        entityId={property.id}
        canManageNotes={hasPermission(session, "notes.manage")}
        canManageDocuments={hasPermission(session, "documents.manage")}
      />
    </div>
  );
}

function Detail({
  label: name,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <div className="flex items-center justify-between gap-3 py-3">
        <span className="text-sm text-muted-foreground">{name}</span>
        <span className="text-sm">{children}</span>
      </div>
      <Separator className="last:hidden" />
    </>
  );
}
