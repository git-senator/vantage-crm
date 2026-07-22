"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { FileText, Loader2, Paperclip, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ClientApiError } from "@/lib/api/client";
import {
  deleteAttachment,
  registerAttachment,
} from "@/lib/api/attachments-client";
import type {
  Attachment,
  AttachmentStatus,
  RecordEntityType,
} from "@/lib/api/types";

const STATUS_LABEL: Record<AttachmentStatus, string> = {
  pending_upload: "Awaiting upload",
  available: "Available",
  quarantined: "Quarantined",
  failed: "Failed",
};

function formatSize(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Attachments on a record. Phase 2.8 records file *metadata* only — object
 * storage is Phase 3 — so choosing a file registers it in `pending_upload` and
 * the panel is honest about the bytes not being stored yet. The list, statuses
 * and delete are all real; the upload is the one thing that waits.
 */
export function AttachmentsPanel({
  entityType,
  entityId,
  attachments,
  canManage,
}: {
  entityType: RecordEntityType;
  entityId: string;
  attachments: Attachment[];
  canManage: boolean;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setPending(true);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Something went wrong. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    void run(async () => {
      await registerAttachment({
        entity_type: entityType,
        entity_id: entityId,
        filename: file.name,
        content_type: file.type || "application/octet-stream",
      });
      if (inputRef.current) inputRef.current.value = "";
    });
  }

  return (
    <div className="space-y-3">
      {canManage && (
        <div className="space-y-1.5">
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            onChange={handleFile}
            disabled={pending}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => inputRef.current?.click()}
            disabled={pending}
          >
            {pending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Paperclip className="size-4" />
            )}
            Attach file
          </Button>
          <p className="text-[11px] text-muted-foreground">
            Files are recorded now; upload &amp; download arrive in a later release.
          </p>
        </div>
      )}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {attachments.length === 0 ? (
        <p className="text-sm text-muted-foreground">No files attached.</p>
      ) : (
        <ul className="space-y-1.5">
          {attachments.map((attachment) => (
            <li
              key={attachment.id}
              className="flex items-center gap-2.5 rounded-lg border bg-card p-2.5"
            >
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">
                  {attachment.filename}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {formatSize(attachment.size_bytes)} ·{" "}
                  {attachment.uploader?.full_name ?? "Unknown"}
                </p>
              </div>
              <Badge
                variant={
                  attachment.status === "available" ? "default" : "secondary"
                }
              >
                {STATUS_LABEL[attachment.status]}
              </Badge>
              {canManage && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-destructive"
                  onClick={() => run(() => deleteAttachment(attachment.id))}
                  disabled={pending}
                  title="Remove"
                >
                  <Trash2 className="size-4" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
