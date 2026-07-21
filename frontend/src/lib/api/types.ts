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

// ------------------------------------------------------------------- leads

export type LeadStage =
  | "new"
  | "contacted"
  | "qualified"
  | "touring"
  | "unqualified";
export type LeadStatus = "open" | "converted" | "lost";
export type LeadTemperature = "hot" | "warm" | "cold";
export type LeadSource =
  | "zillow"
  | "website"
  | "referral"
  | "open_house"
  | "instagram"
  | "cold_call"
  | "realtor_com"
  | "other";

export interface LeadOwner {
  id: string;
  full_name: string;
  initials: string;
  avatar_hue: number;
}

export interface Lead {
  id: string;
  first_name: string;
  last_name: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  stage: LeadStage;
  status: LeadStatus;
  source: LeadSource;
  temperature: LeadTemperature;
  /** NUMERIC arrives as a string so precision survives JSON. */
  budget_min: string | null;
  budget_max: string | null;
  currency: string;
  preferred_location: string | null;
  notes: string | null;
  tags: string[];
  score: number | null;
  last_contacted_at: string | null;
  custom_fields: Record<string, unknown>;
  owner: LeadOwner | null;
  created_at: string;
  updated_at: string;
}

export interface LeadInput {
  first_name: string;
  last_name: string;
  email?: string | null;
  phone?: string | null;
  stage?: LeadStage;
  source?: LeadSource;
  temperature?: LeadTemperature;
  budget_min?: string | null;
  budget_max?: string | null;
  currency?: string;
  preferred_location?: string | null;
  notes?: string | null;
  tags?: string[];
  owner_id?: string | null;
}

export interface LeadFilters {
  search?: string;
  stage?: LeadStage;
  status?: LeadStatus;
  source?: LeadSource;
  temperature?: LeadTemperature;
  owner_id?: string;
  tag?: string;
  limit?: number;
  cursor?: string;
}

export interface PageMeta {
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
}

export interface Page<T> {
  data: T[];
  meta: PageMeta;
}
