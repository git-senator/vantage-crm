"use client";

import { apiRequest } from "@/lib/api/client";
import type { Attachment, AttachmentInput } from "@/lib/api/types";

/**
 * Attachment mutations from the browser.
 *
 * `registerAttachment` records a file's metadata; it does not upload bytes —
 * object storage is Phase 3. The row lands in `pending_upload`. `requestUploadUrl`
 * is the seam for the Phase 3 presigned PUT and returns 501 today.
 */

export async function registerAttachment(
  input: AttachmentInput,
): Promise<Attachment> {
  return apiRequest<Attachment>("/attachments", { method: "POST", body: input });
}

export async function deleteAttachment(id: string): Promise<void> {
  return apiRequest<void>(`/attachments/${id}`, { method: "DELETE" });
}
