import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { DocumentFilters, DocumentLibrary } from "@/lib/api/types";

/**
 * Server-side reads for the workspace document library (the Documents page).
 *
 * Attachments are stored per record; this is the operator-level view across
 * every record, backed by `GET /attachments/library`. Uploads still happen from
 * a record's page (the attachments panel), which is where a file has a home.
 */
export async function getDocumentLibrary(
  filters: DocumentFilters = {},
): Promise<DocumentLibrary> {
  return apiFetch<DocumentLibrary>(`/attachments/library${toQuery(filters)}`);
}
