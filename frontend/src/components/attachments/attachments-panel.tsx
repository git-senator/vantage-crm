"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import {
  Download,
  FileText,
  Loader2,
  Paperclip,
  ShieldAlert,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/i18n/language-provider";
import type { TranslateFn } from "@/i18n/translate";
import { ClientApiError } from "@/lib/api/client";
import {
  UploadTransferError,
  deleteAttachment,
  downloadAttachment,
  formatBytes,
  uploadAttachment,
} from "@/lib/api/attachments-client";
import type {
  Attachment,
  AttachmentEntityType,
  AttachmentStatus,
} from "@/lib/api/types";

const STATUS_KEY: Record<AttachmentStatus, string> = {
  pending_upload: "apProcessing",
  available: "apAvailable",
  quarantined: "apQuarantined",
  failed: "apRejected",
};

/**
 * Attachments on a record.
 *
 * Choosing a file runs the full workflow — register, transfer straight to
 * object storage, then server-side verification — and a row only reads
 * `Available` once the server has read the bytes back and confirmed they are
 * what they claim to be. A file that fails that check comes back as `Rejected`
 * with the reason, which is deliberately shown: "it silently did not upload" is
 * the worst version of this interaction.
 */
export function AttachmentsPanel({
  entityType,
  entityId,
  attachments,
  canManage,
}: {
  entityType: AttachmentEntityType;
  entityId: string;
  attachments: Attachment[];
  canManage: boolean;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState(false);
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setPending(true);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (caught) {
      setError(describe(caught, t));
    } finally {
      setPending(false);
      setUploadingName(null);
    }
  }

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadingName(file.name);
    void run(async () => {
      await uploadAttachment(
        { entity_type: entityType, entity_id: entityId },
        file,
      );
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
            {uploadingName ? t("body.apUploading") : t("body.apAttachFile")}
          </Button>
          {uploadingName && (
            <p className="text-[11px] text-muted-foreground">{uploadingName}</p>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {attachments.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("body.apNoFiles")}</p>
      ) : (
        <ul className="space-y-1.5">
          {attachments.map((attachment) => {
            const isAvailable = attachment.status === "available";
            const isRejected =
              attachment.status === "failed" ||
              attachment.status === "quarantined";
            return (
              <li
                key={attachment.id}
                className="flex items-center gap-2.5 rounded-lg border bg-card p-2.5"
              >
                {isRejected ? (
                  <ShieldAlert className="size-4 shrink-0 text-destructive" />
                ) : (
                  <FileText className="size-4 shrink-0 text-muted-foreground" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {attachment.filename}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {attachment.size_bytes === null
                      ? "—"
                      : formatBytes(attachment.size_bytes)}{" "}
                    · {attachment.uploader?.full_name ?? t("body.apUnknown")}
                  </p>
                  {isRejected && attachment.failure_reason && (
                    <p className="text-[11px] text-destructive">
                      {attachment.failure_reason}
                    </p>
                  )}
                </div>
                <Badge
                  variant={
                    isAvailable
                      ? "default"
                      : isRejected
                        ? "destructive"
                        : "secondary"
                  }
                >
                  {t(`body.${STATUS_KEY[attachment.status]}`)}
                </Badge>
                {isAvailable && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => run(() => downloadAttachment(attachment.id))}
                    disabled={pending}
                    title={t("body.apDownloadName", {
                      name: attachment.filename,
                    })}
                  >
                    <Download className="size-4" />
                  </Button>
                )}
                {canManage && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="text-destructive"
                    onClick={() => run(() => deleteAttachment(attachment.id))}
                    disabled={pending}
                    title={t("buttons.remove")}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function describe(caught: unknown, t: TranslateFn): string {
  if (caught instanceof UploadTransferError) return caught.message;
  if (caught instanceof ClientApiError) return caught.message;
  return t("body.errGeneric");
}
