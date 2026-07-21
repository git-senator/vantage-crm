/**
 * API response contracts.
 *
 * Hand-written for Phase 1 because the surface is small and stable. Phase 2
 * generates these from the OpenAPI schema (`openapi-typescript`) so a backend
 * change that the frontend has not absorbed becomes a failed typecheck rather
 * than a runtime error — see docs/ARCHITECTURE.md §5.2 and risk R4.
 *
 * Safe to import from client components: types only, erased at compile time.
 */

export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
}

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  initials: string;
  job_title: string | null;
  phone: string | null;
  avatar_hue: number;
  status: string;
  mfa_enabled: boolean;
  last_login_at: string | null;
  organization: OrganizationSummary;
  roles: string[];
  permissions: string[];
}

export interface SessionResponse {
  user: UserProfile;
  expires_at: string;
  csrf_token: string;
}

/** RFC 7807 problem+json — the error shape every endpoint returns. */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  request_id?: string;
  errors?: { field: string; message: string }[];
}
