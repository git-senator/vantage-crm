import type { Metadata } from "next";
import Link from "next/link";
import { FileCheck2, FileText, HardDrive, ShieldAlert, UploadCloud } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { DocumentTable } from "@/components/documents/document-table";
import { EmptyState } from "@/components/shared/empty-state";
import { Card } from "@/components/ui/card";
import { getDocumentLibrary } from "@/lib/api/documents";
import type { AttachmentStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Documents" };

const FILTERS: { value: AttachmentStatus | "all"; label: string }[] = [
  { value: "all", label: "All files" },
  { value: "available", label: "Available" },
  { value: "pending_upload", label: "Pending upload" },
  { value: "quarantined", label: "Quarantined" },
  { value: "failed", label: "Failed" },
];

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const active = (params.status as AttachmentStatus | undefined) ?? undefined;

  const library = await getDocumentLibrary({ status: active, limit: 100 });
  const { data, counts, total } = library;

  const available = counts.available ?? 0;
  const attention = (counts.quarantined ?? 0) + (counts.failed ?? 0);
  const pending = counts.pending_upload ?? 0;

  const stats: Stat[] = [
    { label: "Total documents", value: String(total), hint: "across every record", icon: FileText },
    { label: "Available", value: String(available), hint: "ready to download", icon: FileCheck2 },
    { label: "Needs attention", value: String(attention), hint: "quarantined or failed", icon: ShieldAlert },
    { label: "Pending upload", value: String(pending), hint: "not yet finalized", icon: HardDrive },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Documents"
        description="Every file attached across your leads, clients, properties and deals."
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
          title={active ? "No documents match this filter" : "No documents yet"}
          description={
            active
              ? "Try a different status filter."
              : "Attach files from a lead, client, property or deal — they show up here."
          }
        />
      )}
    </div>
  );
}
