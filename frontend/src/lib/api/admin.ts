import "server-only";

import { apiFetch } from "@/lib/api/server";
import type { AdminOverview } from "@/lib/api/types";

/**
 * The operator's view. Requires `settings.manage`; read-only throughout.
 *
 * One call for the whole dashboard rather than seven: the panels share a window
 * and a permission check, and seven round trips would re-authorise seven times
 * and could render panels captured at different moments.
 */
export async function getAdminOverview(hours = 24): Promise<AdminOverview> {
  return apiFetch<AdminOverview>(`/admin/overview?hours=${hours}`);
}
