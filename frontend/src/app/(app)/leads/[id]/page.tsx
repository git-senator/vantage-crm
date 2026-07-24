import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ArrowLeft, Flame, Mail, MapPin, Pencil, Phone, UserCheck } from "lucide-react";

import { ConvertLeadButton } from "@/components/leads/convert-lead-button";
import { DeleteLeadButton } from "@/components/leads/delete-lead-button";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getLead } from "@/lib/api/leads";
import { ApiError } from "@/lib/api/server";
import { LeadIntelligence } from "@/components/leads/lead-intelligence";
import { RecordActivity } from "@/components/shared/record-activity";
import { hasPermission, requireSession } from "@/lib/auth/session";
import { formatPrice } from "@/lib/format";

export const metadata: Metadata = { title: "Lead" };

function budgetLabel(min: string | null, max: string | null): string {
  const low = min ? formatPrice(Number(min)) : null;
  const high = max ? formatPrice(Number(max)) : null;
  if (low && high) return `${low} – ${high}`;
  return low ?? high ?? "—";
}

function label(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export default async function LeadDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;

  let lead;
  try {
    lead = await getLead(id);
  } catch (error) {
    // The API returns 404 for a lead outside the caller's scope as well as one
    // that does not exist — deliberately indistinguishable, so there is no
    // existence oracle. Rendering the standard not-found page preserves that.
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  const canManage = hasPermission(session, "leads.manage");
  // Conversion needs both, and the API enforces both. Hiding the button
  // without one is UX, not a control — see docs/SECURITY.md §1.4.
  const canConvert =
    canManage && hasPermission(session, "contacts.manage");
  const isConverted = lead.converted_client_id !== null;

  return (
    <div className="space-y-6">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 text-muted-foreground"
        render={<Link href="/leads" />}
      >
        <ArrowLeft className="size-4" />
        All leads
      </Button>

      <PageHeader
        title={lead.full_name}
        description={lead.preferred_location ?? "No location preference recorded"}
        actions={
          canManage ? (
            <>
              {canConvert && !isConverted && (
                <ConvertLeadButton
                  leadId={lead.id}
                  leadName={lead.full_name}
                />
              )}
              <Button
                variant="outline"
                render={<Link href={`/leads/${lead.id}/edit`} />}
              >
                <Pencil className="size-4" />
                Edit
              </Button>
              <DeleteLeadButton leadId={lead.id} leadName={lead.full_name} />
            </>
          ) : null
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <Detail label="Stage">
                <StatusBadge status={lead.stage} />
              </Detail>
              <Detail label="Temperature">
                <span className="flex items-center gap-1.5">
                  <StatusBadge status={lead.temperature} />
                  {lead.temperature === "hot" && (
                    <Flame className="size-3.5 text-destructive" />
                  )}
                </span>
              </Detail>
              <Detail label="Source">{label(lead.source)}</Detail>
              <Detail label="Budget">
                <span className="tabular">
                  {budgetLabel(lead.budget_min, lead.budget_max)}
                </span>
              </Detail>
              <Detail label="Status">
                <StatusBadge status={lead.status} />
              </Detail>
              {lead.score !== null && (
                <Detail label="Score">
                  <span className="tabular font-medium">{lead.score}</span>
                </Detail>
              )}
            </CardContent>
          </Card>

          {lead.notes && (
            <Card>
              <CardHeader>
                <CardTitle>Notes</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">
                  {lead.notes}
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <LeadIntelligence leadId={lead.id} />

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Contact</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {lead.email ? (
                <a
                  href={`mailto:${lead.email}`}
                  className="flex items-center gap-2 text-muted-foreground hover:text-foreground"
                >
                  <Mail className="size-4 shrink-0" />
                  <span className="truncate">{lead.email}</span>
                </a>
              ) : null}
              {lead.phone ? (
                <a
                  href={`tel:${lead.phone}`}
                  className="flex items-center gap-2 text-muted-foreground hover:text-foreground"
                >
                  <Phone className="size-4 shrink-0" />
                  {lead.phone}
                </a>
              ) : null}
              {lead.preferred_location ? (
                <p className="flex items-center gap-2 text-muted-foreground">
                  <MapPin className="size-4 shrink-0" />
                  <span className="truncate">{lead.preferred_location}</span>
                </p>
              ) : null}
              {!lead.email && !lead.phone && (
                <p className="text-muted-foreground">No contact details yet.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Owner</CardTitle>
            </CardHeader>
            <CardContent>
              {lead.owner ? (
                <div className="flex items-center gap-3">
                  <UserAvatar
                    user={{
                      id: lead.owner.id,
                      name: lead.owner.full_name,
                      initials: lead.owner.initials,
                      role: "",
                      hue: lead.owner.avatar_hue,
                    }}
                    size="md"
                  />
                  <span className="text-sm font-medium">
                    {lead.owner.full_name}
                  </span>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Unassigned</p>
              )}
            </CardContent>
          </Card>

          {/* The funnel link, once this lead has become a client. Conversion
              is one-shot, so this replaces the convert action rather than
              sitting alongside it. */}
          {isConverted && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Converted</CardTitle>
              </CardHeader>
              <CardContent>
                <Link
                  href={`/clients/${lead.converted_client_id}`}
                  className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
                >
                  <UserCheck className="size-4 shrink-0" />
                  View the client record
                </Link>
              </CardContent>
            </Card>
          )}

          {lead.tags.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Tags</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {lead.tags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <RecordActivity
        entityType="lead"
        entityId={lead.id}
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
