import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ArrowLeft, Building2, Mail, Pencil, Phone, Target } from "lucide-react";

import { DeleteClientButton } from "@/components/clients/delete-client-button";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getClient } from "@/lib/api/clients";
import { ApiError } from "@/lib/api/server";
import { hasPermission, requireSession } from "@/lib/auth/session";
import { formatPrice, titleize } from "@/lib/format";

export const metadata: Metadata = { title: "Client" };

export default async function ClientDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;

  let client;
  try {
    client = await getClient(id);
  } catch (error) {
    // The API returns 404 for a client outside the caller's scope as well as
    // one that does not exist — deliberately indistinguishable, so there is no
    // existence oracle. Rendering the standard not-found page preserves that.
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  const canManage = hasPermission(session, "contacts.manage");

  return (
    <div className="space-y-6">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 text-muted-foreground"
        render={<Link href="/clients" />}
      >
        <ArrowLeft className="size-4" />
        All clients
      </Button>

      <PageHeader
        title={client.display_name}
        description={
          client.is_company
            ? "Company client"
            : `${titleize(client.type)} · client since ${client.client_since ?? "—"}`
        }
        actions={
          canManage ? (
            <>
              <Button
                variant="outline"
                render={<Link href={`/clients/${client.id}/edit`} />}
              >
                <Pencil className="size-4" />
                Edit
              </Button>
              <DeleteClientButton
                clientId={client.id}
                clientName={client.display_name}
              />
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
              <Detail label="Type">
                <StatusBadge
                  status={client.type}
                  label={titleize(client.type)}
                  tone="neutral"
                  dot={false}
                />
              </Detail>
              <Detail label="Status">
                <StatusBadge status={client.status} />
              </Detail>
              <Detail label="Lifetime value">
                <span className="tabular">
                  {client.lifetime_value
                    ? formatPrice(Number(client.lifetime_value))
                    : "—"}
                </span>
              </Detail>
              <Detail label="Client since">
                {client.client_since ?? "—"}
              </Detail>
              {client.is_company && client.first_name && (
                <Detail label="Primary contact">
                  {`${client.first_name} ${client.last_name ?? ""}`.trim()}
                </Detail>
              )}
            </CardContent>
          </Card>

          {client.notes && (
            <Card>
              <CardHeader>
                <CardTitle>Notes</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">
                  {client.notes}
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Contact</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {client.email ? (
                <a
                  href={`mailto:${client.email}`}
                  className="flex items-center gap-2 text-muted-foreground hover:text-foreground"
                >
                  <Mail className="size-4 shrink-0" />
                  <span className="truncate">{client.email}</span>
                </a>
              ) : null}
              {client.phone ? (
                <a
                  href={`tel:${client.phone}`}
                  className="flex items-center gap-2 text-muted-foreground hover:text-foreground"
                >
                  <Phone className="size-4 shrink-0" />
                  {client.phone}
                </a>
              ) : null}
              {client.company_name && !client.is_company ? (
                <p className="flex items-center gap-2 text-muted-foreground">
                  <Building2 className="size-4 shrink-0" />
                  <span className="truncate">{client.company_name}</span>
                </p>
              ) : null}
              {!client.email && !client.phone && (
                <p className="text-muted-foreground">No contact details yet.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Owner</CardTitle>
            </CardHeader>
            <CardContent>
              {client.owner ? (
                <div className="flex items-center gap-3">
                  <UserAvatar
                    user={{
                      id: client.owner.id,
                      name: client.owner.full_name,
                      initials: client.owner.initials,
                      role: "",
                      hue: client.owner.avatar_hue,
                    }}
                    size="md"
                  />
                  <span className="text-sm font-medium">
                    {client.owner.full_name}
                  </span>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Unassigned</p>
              )}
            </CardContent>
          </Card>

          {/* The funnel link, when this client came from a lead. */}
          {client.source_lead_id && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Origin</CardTitle>
              </CardHeader>
              <CardContent>
                <Link
                  href={`/leads/${client.source_lead_id}`}
                  className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
                >
                  <Target className="size-4 shrink-0" />
                  Converted from a lead
                </Link>
              </CardContent>
            </Card>
          )}

          {client.tags.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Tags</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {client.tags.map((tag) => (
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
