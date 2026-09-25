import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ArrowLeft, Building2, History, Pencil, User } from "lucide-react";

import { DealStageMover } from "@/components/deals/deal-stage-mover";
import { DeleteDealButton } from "@/components/deals/delete-deal-button";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { DealIntelligence } from "@/components/deals/deal-intelligence";
import { getDeal, getDealHistory, getPipeline } from "@/lib/api/deals";
import { ApiError } from "@/lib/api/server";
import { RecordActivity } from "@/components/shared/record-activity";
import { getTranslations } from "@/i18n/server";
import { stageLabel } from "@/lib/stage-label";
import type { TranslateFn } from "@/i18n/translate";
import { hasPermission, requireSession } from "@/lib/auth/session";
import { formatCurrency, titleize } from "@/lib/format";

export const metadata: Metadata = { title: "Deal" };

/** "3 d", "4 h", "12 min" — enough precision for a cycle time. */
function humaniseDuration(seconds: number | null, t: TranslateFn): string {
  if (seconds === null) return "—";
  const days = Math.floor(seconds / 86_400);
  if (days >= 1) return t("body.dvDurDays", { n: days });
  const hours = Math.floor(seconds / 3_600);
  if (hours >= 1) return t("body.dvDurHours", { n: hours });
  const minutes = Math.max(1, Math.floor(seconds / 60));
  return t("body.dvDurMinutes", { n: minutes });
}

export default async function DealDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const t = await getTranslations();

  let deal;
  try {
    deal = await getDeal(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  // The stage list comes from the deal's own pipeline, so the mover can only
  // offer stages the server would accept — moving across pipelines is a 409.
  const [history, pipeline] = await Promise.all([
    getDealHistory(deal.id),
    getPipeline(deal.pipeline_id),
  ]);

  const canManage = hasPermission(session, "deals.manage");

  return (
    <div className="space-y-6">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 text-muted-foreground"
        render={<Link href="/deals" />}
      >
        <ArrowLeft className="size-4" />
        {t("body.dvBackDeals")}
      </Button>

      <PageHeader
        title={deal.title}
        description={`${deal.client.display_name} · ${stageLabel(deal.stage.name, t)}`}
        actions={
          canManage ? (
            <>
              <DealStageMover deal={deal} stages={pipeline.stages} />
              <Button
                variant="outline"
                render={<Link href={`/deals/${deal.id}/edit`} />}
              >
                <Pencil className="size-4" />
                {t("buttons.edit")}
              </Button>
              <DeleteDealButton dealId={deal.id} dealTitle={deal.title} />
            </>
          ) : null
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>{t("body.dvFinancials")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <Detail label={t("body.dfValue")}>
                <span className="tabular font-medium">
                  {deal.value ? formatCurrency(Number(deal.value)) : "—"}
                </span>
              </Detail>
              <Detail label={t("body.dvCommission")}>
                <span className="tabular">
                  {deal.commission_amount
                    ? formatCurrency(Number(deal.commission_amount))
                    : "—"}
                  {deal.commission_rate && (
                    <span className="ml-1.5 text-muted-foreground">
                      ({(Number(deal.commission_rate) * 100).toFixed(2)}%)
                    </span>
                  )}
                </span>
              </Detail>
              <Detail label={t("body.dvWeightedValue")}>
                <span className="tabular">
                  {deal.weighted_value
                    ? formatCurrency(Number(deal.weighted_value))
                    : "—"}
                  <span className="ml-1.5 text-muted-foreground">
                    {t("body.dvAtPercent", { p: deal.probability })}
                  </span>
                </span>
              </Detail>
              <Detail label={t("body.dfExpectedClose")}>
                {deal.expected_close_date ?? "—"}
              </Detail>
              {deal.actual_close_date && (
                <Detail label={t("body.dvActualClose")}>
                  {deal.actual_close_date}
                </Detail>
              )}
            </CardContent>
          </Card>

          {deal.lost_reason && (
            <Card>
              <CardHeader>
                <CardTitle>{t("body.dvWhyLost")}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">
                  {deal.lost_reason}
                </p>
              </CardContent>
            </Card>
          )}

          {/* The analytics substrate, surfaced. Every row here is a
              deal_stage_history record with its measured time in stage. */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <History className="size-4" />
                {t("body.dvStageHistory")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {history.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("body.dvNoTransitions")}
                </p>
              ) : (
                <ol className="space-y-0">
                  {history.map((entry) => (
                    <li key={entry.id}>
                      <div className="flex items-start justify-between gap-3 py-3">
                        <div className="min-w-0">
                          <p className="text-sm">
                            {entry.from_stage ? (
                              <>
                                <span className="text-muted-foreground">
                                  {stageLabel(entry.from_stage.name, t)}
                                </span>
                                <span className="mx-1.5 text-muted-foreground">
                                  →
                                </span>
                                <span className="font-medium">
                                  {stageLabel(entry.to_stage.name, t)}
                                </span>
                              </>
                            ) : (
                              <>
                                <span className="font-medium">
                                  {stageLabel(entry.to_stage.name, t)}
                                </span>
                                <span className="ml-1.5 text-muted-foreground">
                                  {t("body.dvCreatedTag")}
                                </span>
                              </>
                            )}
                          </p>
                          {entry.note && (
                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {entry.note}
                            </p>
                          )}
                        </div>
                        <div className="shrink-0 text-right">
                          <p className="text-xs text-muted-foreground">
                            {new Date(entry.changed_at).toLocaleDateString()}
                          </p>
                          {entry.duration_seconds !== null && (
                            <p className="tabular text-xs text-muted-foreground">
                              {t("body.dvInStage", {
                                d: humaniseDuration(entry.duration_seconds, t),
                              })}
                            </p>
                          )}
                        </div>
                      </div>
                      <Separator className="last:hidden" />
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <DealIntelligence dealId={deal.id} />

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("forms.status")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">
                  {t("forms.stage")}
                </span>
                <StatusBadge
                  status={deal.stage.key}
                  label={stageLabel(deal.stage.name, t)}
                  tone="neutral"
                  dot={false}
                />
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">
                  {t("body.dvOutcome")}
                </span>
                <StatusBadge
                  status={deal.status}
                  label={t(`body.dealStatus_${deal.status}`)}
                />
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">
                  {t("body.dfPriority")}
                </span>
                <StatusBadge
                  status={deal.priority}
                  label={t(`body.dfPriority_${deal.priority}`)}
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("body.dvClient")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Link
                href={`/clients/${deal.client.id}`}
                className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
              >
                <User className="size-4 shrink-0" />
                <span className="truncate">{deal.client.display_name}</span>
              </Link>
            </CardContent>
          </Card>

          {deal.listing && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t("body.dfProperty")}</CardTitle>
              </CardHeader>
              <CardContent>
                <Link
                  href={`/properties/${deal.listing.id}`}
                  className="flex items-start gap-2 text-sm text-muted-foreground hover:text-foreground"
                >
                  <Building2 className="mt-0.5 size-4 shrink-0" />
                  <span className="min-w-0">
                    <span className="block truncate">{deal.listing.title}</span>
                    <span className="block truncate text-xs">
                      {deal.listing.full_address}
                    </span>
                  </span>
                </Link>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("forms.owner")}</CardTitle>
            </CardHeader>
            <CardContent>
              {deal.owner ? (
                <div className="flex items-center gap-3">
                  <UserAvatar
                    user={{
                      id: deal.owner.id,
                      name: deal.owner.full_name,
                      initials: deal.owner.initials,
                      role: "",
                      hue: deal.owner.avatar_hue,
                    }}
                    size="md"
                  />
                  <span className="text-sm font-medium">
                    {deal.owner.full_name}
                  </span>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {t("body.unassigned")}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("body.dvPipeline")}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                {titleize(pipeline.name)}
              </p>
            </CardContent>
          </Card>
        </div>
      </div>

      <RecordActivity
        entityType="deal"
        entityId={deal.id}
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
