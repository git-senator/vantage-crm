import type { Metadata } from "next";
import Link from "next/link";
import { FileCheck2, FileText, HardDrive, ShieldAlert, UploadCloud } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { DocumentTable } from "@/components/documents/document-table";
import { EmptyState } from "@/components/shared/empty-state";
import { Card } from "@/components/ui/card";
import { getDocumentLibrary } from "@/lib/api/documents";
import { getTranslations } from "@/i18n/server";
import type { AttachmentStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Documents" };

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const t = await getTranslations();
  const active = (params.status as AttachmentStatus | undefined) ?? undefined;

  const FILTERS: { value: AttachmentStatus | "all"; label: string }[] = [
    { value: "all", label: t("body.docAll") },
    { value: "available", label: t("body.docAvailable") },
    { value: "pending_upload", label: t("body.docPending") },
    { value: "quarantined", label: t("body.docQuarantined") },
    { value: "failed", label: t("body.docFailed") },
  ];

  const library = await getDocumentLibrary({ status: active, limit: 100 });
  const { data, counts, total } = library;

  const available = counts.available ?? 0;
  const attention = (counts.quarantined ?? 0) + (counts.failed ?? 0);
  const pending = counts.pending_upload ?? 0;

  const stats: Stat[] = [
    { label: t("body.docTotal"), value: String(total), hint: t("body.docTotalHint"), icon: FileText },
    { label: t("body.docAvailable"), value: String(available), hint: t("body.docAvailableHint"), icon: FileCheck2 },
    { label: t("body.docNeedsAttention"), value: String(attention), hint: t("body.docNeedsAttentionHint"), icon: ShieldAlert },
    { label: t("body.docPending"), value: String(pending), hint: t("body.docPendingHint"), icon: HardDrive },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.documentsTitle")}
        description={t("body.documentsDesc")}
      />

      <StatGrid stats={stats} />

      {/* Status filters — server-side, via the query string. */}
      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => {
          const isActive = (active ?? "all") === f.value;
          const href = f.value === "all" ? "/documents" : `/documents?status=${f.value}`;
          return (
            <Link
              key={f.value}
              href={href}
              className={cn(
                "rounded-full border px-3 py-1 text-sm transition-colors",
                isActive
                  ? "border-transparent bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {f.label}
            </Link>
          );
        })}
      </div>

      {data.length > 0 ? (
        <Card className="overflow-hidden p-0">
          <DocumentTable documents={data} />
        </Card>
      ) : (
        <EmptyState
          icon={UploadCloud}
          title={active ? t("body.docEmptyFiltered") : t("body.docEmptyTitle")}
          description={
            active ? t("body.docEmptyFilteredDesc") : t("body.docEmptyDesc")
          }
        />
      )}
    </div>
  );
}
