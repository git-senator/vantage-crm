import "server-only";

import { apiFetch } from "@/lib/api/server";
import type {
  IntegrationsStatus,
  Organization,
  OrganizationMember,
} from "@/lib/api/types";

/** The caller's workspace, including its persisted settings. */
export async function getOrganization(): Promise<Organization> {
  return apiFetch<Organization>("/organizations/current");
}

/** Everyone in the workspace, with their role. */
export async function listMembers(): Promise<OrganizationMember[]> {
  return apiFetch<OrganizationMember[]>("/organizations/current/members");
}

/** Which real integrations are wired (Google Calendar today). */
export async function getIntegrations(): Promise<IntegrationsStatus> {
  return apiFetch<IntegrationsStatus>("/organizations/current/integrations");
}
