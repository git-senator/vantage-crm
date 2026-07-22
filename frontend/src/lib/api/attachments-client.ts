"use client";

import { ClientApiError, apiRequest } from "@/lib/api/client";
import type {
  Attachment,
  AttachmentInput,
  AttachmentRegistered,
  PresignedDownload,
  PresignedUpload,
} from "@/lib/api/types";

/**
 * Attachment upload, from the browser.
 *
 * The bytes never touch our own server. `uploadAttachment` registers the file
 * with the API, PUTs it **directly to object storage** using the short-lived
 * credential the API returns, then asks the API to verify and publish it.
 * Three small round trips to us, one large transfer to storage.
 *
 * Two consequences worth knowing before changing this file:
 *
 *  * The PUT is a plain `fetch` to a foreign origin — deliberately *not*
 *    `apiRequest`. It must not carry our cookies or CSRF header, because those
 *    are credentials for a different service and sending them to a third party
 *    is how they leak.
 *  * The upload is only real once `finalize` returns. A UI that showed the file
 *    as attached after the PUT would be showing an unverified file the server
 *    may yet reject.
 */

/** Registration: the row, plus where to put the bytes. */
export async function registerAttachment(
  input: AttachmentInput,
): Promise<AttachmentRegistered> {
  return apiRequest<AttachmentRegistered>("/attachments", {
    method: "POST",
    body: input,
  });
}

/** Verify what was uploaded and publish it. */
export async function finalizeAttachment(id: string): Promise<Attachment> {
  return apiRequest<Attachment>(`/attachments/${id}/finalize`, {
    method: "POST",
    body: {},
  });
}

export async function deleteAttachment(id: string): Promise<void> {
  return apiRequest<void>(`/attachments/${id}`, { method: "DELETE" });
}

/** A fresh upload credential for a registration still awaiting its bytes. */
export async function requestUploadUrl(id: string): Promise<PresignedUpload> {
  return apiRequest<PresignedUpload>(`/attachments/${id}/upload-url`, {
    method: "POST",
    body: {},
  });
}

export async function requestDownloadUrl(
  id: string,
): Promise<PresignedDownload> {
  return apiRequest<PresignedDownload>(`/attachments/${id}/download-url`);
}

/** Raised when the transfer to object storage itself fails. */
export class UploadTransferError extends Error {
  constructor(readonly status: number) {
    super(
      status === 0
        ? "The upload could not reach file storage."
        : `File storage rejected the upload (${status}).`,
    );
    this.name = "UploadTransferError";
  }
}

async function putToStorage(
  upload: PresignedUpload,
  file: File,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(upload.url, {
      method: "PUT",
      // Exactly the headers the signature covers. Adding others is harmless;
      // omitting one of these makes storage reject the request.
      headers: upload.required_headers,
      body: file,
      // No cookies, no CSRF header: this is a different origin, and our
      // session credentials have no business being sent to it.
      credentials: "omit",
      mode: "cors",
    });
  } catch {
    // A network-level failure — including the CORS rejection you get from a
    // misconfigured bucket — surfaces as a thrown TypeError with no status.
    throw new UploadTransferError(0);
  }
  if (!response.ok) throw new UploadTransferError(response.status);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The whole workflow: register, transfer, verify.
 *
 * Returns the published attachment. If the server rejects the file at
 * finalization — wrong type, too large — the error propagates and the row is
 * already marked `failed` with a reason, so a caller that refreshes shows the
 * failure rather than a phantom pending file.
 */
export async function uploadAttachment(
  input: Omit<AttachmentInput, "filename" | "content_type">,
  file: File,
): Promise<Attachment> {
  const registered = await registerAttachment({
    ...input,
    filename: file.name,
    // A browser leaves this empty for types it does not recognise. The server
    // re-derives the truth from the bytes either way; this is only the claim.
    content_type: file.type || "application/octet-stream",
  });

  // Checked before the transfer so an obviously oversized file costs nothing.
  // The server checks again from the size storage reports, which is the number
  // that actually counts.
  if (file.size > registered.upload.max_bytes) {
    throw new ClientApiError(
      413,
      null,
      `Files must be smaller than ${formatBytes(registered.upload.max_bytes)}.`,
    );
  }

  await putToStorage(registered.upload, file);
  return finalizeAttachment(registered.attachment.id);
}

/** Fetch a signed URL and hand it to the browser to download. */
export async function downloadAttachment(id: string): Promise<void> {
  const { url, filename } = await requestDownloadUrl(id);
  const anchor = document.createElement("a");
  anchor.href = url;
  // Advisory only — the signed URL already carries a Content-Disposition that
  // storage returns, and that is what actually names the saved file.
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}
