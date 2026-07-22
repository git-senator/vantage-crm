import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { Attachment, RecordEntityType } from "@/lib/api/types";

/** A record's attachments. Metadata only in Phase 2.8 — the bytes are Phase 3. */
export async function listAttachmentsForEntity(
  entityType: RecordEntityType,
  entityId: string,
): Promise<Attachment[]> {
  return apiFetch<Attachment[]>(
    `/attachments${toQuery({ entity_type: entityType, entity_id: entityId })}`,
  );
}
