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

/**
 * The owner projection every scoped entity returns.
 *
 * One shape rather than one per entity: the backend serialises the same four
 * fields for leads and clients, and duplicating the interface is how the two
 * drift apart.
 */
export interface OwnerSummary {
  id: string;
  full_name: string;
  initials: string;
  avatar_hue: number;
}

export type LeadOwner = OwnerSummary;

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
  /** Non-null once this lead has been converted. Conversion is one-shot. */
  converted_client_id: string | null;
  converted_at: string | null;
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

// ----------------------------------------------------------------- clients

export type ClientType =
  | "buyer"
  | "seller"
  | "investor"
  | "landlord"
  | "tenant"
  | "other";
export type ClientStatus = "active" | "under_contract" | "dormant" | "past";

export interface Client {
  id: string;
  /** Null for a company-only client. */
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  /** Company name when present, otherwise the person's name. Always set. */
  display_name: string;
  is_company: boolean;
  email: string | null;
  phone: string | null;
  type: ClientType;
  status: ClientStatus;
  address: Record<string, unknown>;
  /** NUMERIC arrives as a string so precision survives JSON. */
  lifetime_value: string | null;
  currency: string;
  client_since: string | null;
  notes: string | null;
  tags: string[];
  custom_fields: Record<string, unknown>;
  /** Set when this client came from a lead. Read-only. */
  source_lead_id: string | null;
  owner: OwnerSummary | null;
  created_at: string;
  updated_at: string;
}

export interface ClientInput {
  first_name?: string | null;
  last_name?: string | null;
  company_name?: string | null;
  email?: string | null;
  phone?: string | null;
  type?: ClientType;
  status?: ClientStatus;
  address?: Record<string, unknown>;
  lifetime_value?: string | null;
  currency?: string;
  client_since?: string | null;
  notes?: string | null;
  tags?: string[];
  owner_id?: string | null;
}

export interface ClientFilters {
  search?: string;
  type?: ClientType;
  status?: ClientStatus;
  owner_id?: string;
  tag?: string;
  limit?: number;
  cursor?: string;
}

/** What conversion needs beyond what the lead already carries. */
export interface ConvertLeadInput {
  type?: ClientType;
  company_name?: string | null;
  client_since?: string | null;
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
