import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { Attachment, AttachmentEntityType } from "@/lib/api/types";

/**
 * A record's attachments.
 *
 * Metadata only — no download URLs. Signing one per row so a user can click one
 * of them would mint a page's worth of bearer credentials for nothing; the
 * panel asks for a URL at the moment of the click instead.
 */
export async function listAttachmentsForEntity(
  entityType: AttachmentEntityType,
  entityId: string,
): Promise<Attachment[]> {
  return apiFetch<Attachment[]>(
    `/attachments${toQuery({ entity_type: entityType, entity_id: entityId })}`,
  );
}
