import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { Note, Page, RecordEntityType } from "@/lib/api/types";

/**
 * Server-side note reads, for React Server Components.
 *
 * Marked server-only for the same reason as every other read module: a client
 * component importing this would ship the internal API host. Mutations go
 * through `notes-client.ts` and the same-origin BFF proxy.
 */

/** A record's notes, pinned first then newest. */
export async function listNotesForEntity(
  entityType: RecordEntityType,
  entityId: string,
): Promise<Note[]> {
  const page = await apiFetch<Page<Note>>(
    `/notes${toQuery({ entity_type: entityType, entity_id: entityId })}`,
  );
  return page.data;
}
