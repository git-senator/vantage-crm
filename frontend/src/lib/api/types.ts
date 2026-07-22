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

// -------------------------------------------------------------- properties

export type PropertyStatus =
  | "active"
  | "pending"
  | "sold"
  | "off_market"
  | "coming_soon";
export type PropertyType =
  | "single_family"
  | "condo"
  | "townhouse"
  | "multi_family"
  | "land"
  | "commercial";

export interface Property {
  id: string;
  title: string;
  mls_number: string | null;
  status: PropertyStatus;
  property_type: PropertyType;

  address_line1: string;
  address_line2: string | null;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  /** Server-composed single-line address. Always set. */
  full_address: string;

  /** NUMERIC arrives as a string so precision survives JSON. */
  latitude: string | null;
  longitude: string | null;
  price: string | null;
  currency: string;

  bedrooms: number | null;
  /** NUMERIC(3,1) — half-baths are real, so this is not an integer. */
  bathrooms: string | null;
  square_feet: number | null;
  lot_size_sqft: number | null;
  year_built: number | null;

  listed_at: string | null;
  /** Derived from listed_at server-side; null once sold. */
  days_on_market: number | null;
  view_count: number;
  save_count: number;

  description: string | null;
  features: string[];
  custom_fields: Record<string, unknown>;

  /** The seller this listing belongs to. */
  client_id: string | null;
  listing_agent: OwnerSummary | null;
  created_at: string;
  updated_at: string;
}

export interface PropertyInput {
  title: string;
  mls_number?: string | null;
  status?: PropertyStatus;
  property_type?: PropertyType;

  address_line1: string;
  address_line2?: string | null;
  city: string;
  state: string;
  postal_code: string;
  country?: string;

  latitude?: string | null;
  longitude?: string | null;
  price?: string | null;
  currency?: string;

  bedrooms?: number | null;
  bathrooms?: string | null;
  square_feet?: number | null;
  lot_size_sqft?: number | null;
  year_built?: number | null;

  listed_at?: string | null;
  description?: string | null;
  features?: string[];

  client_id?: string | null;
  listing_agent_id?: string | null;
}

export interface PropertyFilters {
  search?: string;
  status?: PropertyStatus;
  property_type?: PropertyType;
  listing_agent_id?: string;
  client_id?: string;
  city?: string;
  min_price?: string;
  max_price?: string;
  min_bedrooms?: number;
  feature?: string;
  limit?: number;
  cursor?: string;
}

// ------------------------------------------------- pipelines and deals

export interface PipelineStage {
  id: string;
  /** Machine name. Analytics group by this; the UI labels from `name`. */
  key: string;
  name: string;
  position: number;
  default_probability: number;
  is_won: boolean;
  is_lost: boolean;
  is_terminal: boolean;
}

export interface Pipeline {
  id: string;
  name: string;
  description: string | null;
  is_default: boolean;
  stages: PipelineStage[];
  created_at: string;
  updated_at: string;
}

export type DealPriority = "low" | "medium" | "high" | "urgent";
/** Derived server-side from the stage. Never sent on a write. */
export type DealStatus = "open" | "won" | "lost";

export interface DealStageSummary {
  id: string;
  key: string;
  name: string;
  position: number;
  is_won: boolean;
  is_lost: boolean;
}

export interface Deal {
  id: string;
  title: string;
  status: DealStatus;
  /** NUMERIC arrives as a string so precision survives JSON. */
  value: string | null;
  currency: string;
  commission_amount: string | null;
  /** 0.0250 is 2.5%. */
  commission_rate: string | null;
  /** value x probability. Null when the deal is unpriced. */
  weighted_value: string | null;
  probability: number;
  priority: DealPriority;
  expected_close_date: string | null;
  actual_close_date: string | null;
  lost_reason: string | null;
  custom_fields: Record<string, unknown>;

  pipeline_id: string;
  stage: DealStageSummary;
  client: { id: string; display_name: string };
  listing: { id: string; title: string; full_address: string } | null;
  owner: OwnerSummary | null;

  created_at: string;
  updated_at: string;
}

export interface DealInput {
  title: string;
  client_id: string;
  property_id?: string | null;
  value?: string | null;
  currency?: string;
  commission_amount?: string | null;
  commission_rate?: string | null;
  priority?: DealPriority;
  expected_close_date?: string | null;
  /** Create only. Omit both to land in the default pipeline's first stage. */
  pipeline_id?: string | null;
  stage_id?: string | null;
  probability?: number | null;
  owner_id?: string | null;
}

export interface DealFilters {
  search?: string;
  status?: DealStatus;
  pipeline_id?: string;
  stage_id?: string;
  owner_id?: string;
  client_id?: string;
  property_id?: string;
  priority?: DealPriority;
  min_value?: string;
  max_value?: string;
  limit?: number;
  cursor?: string;
}

/**
 * Moving a deal is a domain action, not a field edit — it writes history,
 * resets probability and sets a close date. Hence a dedicated payload rather
 * than a `stage_id` on DealInput.
 */
export interface DealStageTransitionInput {
  to_stage_id: string;
  note?: string | null;
  /** Required by the server when the target stage is a losing one. */
  lost_reason?: string | null;
  probability?: number | null;
}

export interface DealStageHistoryEntry {
  id: string;
  from_stage: DealStageSummary | null;
  to_stage: DealStageSummary;
  changed_by: OwnerSummary | null;
  changed_at: string;
  /** Seconds spent in `from_stage`. Null on the creation row. */
  duration_seconds: number | null;
  note: string | null;
}

export interface DealBoardColumn {
  stage: DealStageSummary;
  deals: Deal[];
  total_value: string;
  count: number;
}

export interface DealBoard {
  pipeline_id: string;
  pipeline_name: string;
  columns: DealBoardColumn[];
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

// --- Notes, Timeline, Attachments (Phase 2.8) ---

/** Entities that notes and the timeline hang off. */
export type RecordEntityType = "lead" | "client" | "property" | "deal" | "task";

/**
 * Attachments accept everything above plus `note` (Phase 3.1) — a note is a
 * document in its own right and people expect to attach to it.
 */
export type AttachmentEntityType = RecordEntityType | "note";

export type NoteContentFormat = "markdown" | "html" | "plain";

export interface Note {
  id: string;
  entity_type: RecordEntityType;
  entity_id: string;
  title: string | null;
  body: string;
  content_format: NoteContentFormat;
  is_pinned: boolean;
  author: OwnerSummary | null;
  /** True when the caller wrote it — the UI shows edit controls only then. */
  is_own: boolean;
  created_at: string;
  updated_at: string;
}

export interface NoteInput {
  entity_type: RecordEntityType;
  entity_id: string;
  title?: string | null;
  body: string;
  content_format?: NoteContentFormat;
  is_pinned?: boolean;
}

export type TimelineKind = "activity" | "note";

export interface TimelineItem {
  kind: TimelineKind;
  id: string;
  entity_type: string;
  entity_id: string;
  /** occurred_at for activities, created_at for notes — the merge axis. */
  timestamp: string;
  type: string;
  title: string | null;
  body: string | null;
  actor: OwnerSummary | null;
  is_system: boolean;
  is_pinned: boolean;
  metadata: Record<string, unknown>;
}

export type AttachmentStatus =
  | "pending_upload"
  | "available"
  | "quarantined"
  | "failed";

export type AttachmentScanStatus =
  | "pending"
  | "clean"
  | "infected"
  | "skipped"
  | "failed";

export interface Attachment {
  id: string;
  entity_type: AttachmentEntityType;
  entity_id: string;
  filename: string;
  content_type: string;
  /** Read back from storage at finalization — never a client-declared number. */
  size_bytes: number | null;
  checksum_sha256: string | null;
  status: AttachmentStatus;
  scan_status: AttachmentScanStatus;
  storage_backend: string;
  uploader: OwnerSummary | null;
  upload_expires_at: string | null;
  available_at: string | null;
  /** Why a `failed` or `quarantined` file is not being served. User-facing. */
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface AttachmentInput {
  entity_type: AttachmentEntityType;
  entity_id: string;
  filename: string;
  content_type: string;
}

/**
 * A credential to write exactly one object, straight to storage.
 *
 * `required_headers` is not advisory — the signature covers them, so a PUT that
 * drops `Content-Type` is rejected by storage itself. Send them verbatim.
 */
export interface PresignedUpload {
  url: string;
  expires_at: string;
  required_headers: Record<string, string>;
  max_bytes: number;
}

export interface AttachmentRegistered {
  attachment: Attachment;
  upload: PresignedUpload;
}

export interface PresignedDownload {
  url: string;
  expires_at: string;
  filename: string;
}

// --- Tasks (backend since Phase 2.7; typed here for the dashboard queue) ---

export type TaskStatus = "todo" | "in_progress" | "blocked" | "done";
export type TaskPriority = "low" | "medium" | "high" | "urgent";

export interface Task {
  id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  priority: TaskPriority;
  due_at: string | null;
  completed_at: string | null;
  is_overdue: boolean;
  entity_type: RecordEntityType | null;
  entity_id: string | null;
  assignee: OwnerSummary | null;
  created_at: string;
  updated_at: string;
}

// --- Dashboard (Phase 2.8) ---

export interface DashboardSummary {
  leads: { open: number; total: number };
  clients: { total: number };
  properties: { active: number; total: number };
  deals: {
    open_count: number;
    open_value: string;
    weighted_value: string;
    won_this_month_count: number;
    won_this_month_value: string;
  };
  tasks: { open: number; overdue: number };
  recent_activity: TimelineItem[];
}

// --- Notifications (Phase 3.3) ---

export type NotificationCategory =
  | "lead"
  | "deal"
  | "task"
  | "document"
  | "mention"
  | "system";

export interface Notification {
  id: string;
  category: NotificationCategory;
  /** The specific event, e.g. `task.assigned`. Finer than `category`. */
  type: string;
  title: string;
  body: string | null;
  entity_type: string | null;
  entity_id: string | null;
  metadata: Record<string, unknown>;
  /** Null for machine-generated notifications. */
  actor: OwnerSummary | null;
  read_at: string | null;
  created_at: string;
}

export interface NotificationList {
  data: Notification[];
  /** Travels with the list so the bell needs no second round trip. */
  unread_count: number;
}

export interface NotificationPreference {
  category: NotificationCategory;
  in_app: boolean;
  email: boolean;
}

export interface UnreadCount {
  unread_count: number;
}

// --- Conversations & messages (Phase 3.4) ---

export type MessageChannel = "email" | "whatsapp" | "sms";
export type MessageDirection = "inbound" | "outbound";
export type MessageStatus =
  | "queued"
  | "sent"
  | "delivered"
  | "failed"
  | "received";

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  direction: MessageDirection;
  status: MessageStatus;
  from_address: string;
  to_address: string;
  subject: string | null;
  body_text: string;
  /**
   * Stored for fidelity and **not sanitised by the API** — it arrived from
   * outside. Never render it as HTML without sanitising first; `body_text` is
   * the one that is always safe.
   */
  body_html: string | null;
  sender: OwnerSummary | null;
  sent_at: string | null;
  read_at: string | null;
  failure_reason: string | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  channel: MessageChannel;
  /** The counterparty's normalised address — the thread's identity. */
  external_id: string;
  display_name: string | null;
  subject: string | null;
  entity_type: string | null;
  entity_id: string | null;
  owner: OwnerSummary | null;
  last_message_at: string | null;
  last_message_preview: string | null;
  unread_count: number;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: ConversationMessage[];
}

export interface MessageSendInput {
  channel?: MessageChannel;
  to_address: string;
  to_name?: string | null;
  subject?: string | null;
  body_text: string;
  entity_type?: "lead" | "client" | "deal" | null;
  entity_id?: string | null;
}

// --- Calendar (Phase 3.5) ---

export type CalendarEventType =
  | "showing"
  | "call"
  | "meeting"
  | "closing"
  | "open_house"
  | "personal";

export type CalendarEventStatus = "confirmed" | "tentative" | "cancelled";

export interface EventAttendee {
  id: string;
  user_id: string | null;
  email: string | null;
  display_name: string | null;
  response: "needs_action" | "accepted" | "declined" | "tentative";
}

export interface CalendarEvent {
  id: string;
  title: string;
  description: string | null;
  location: string | null;
  event_type: CalendarEventType;
  status: CalendarEventStatus;
  /** Instants, not wall-clock — an all-day event is a flag over a range. */
  starts_at: string;
  ends_at: string;
  is_all_day: boolean;
  reminder_minutes: number | null;
  entity_type: string | null;
  entity_id: string | null;
  owner: OwnerSummary;
  attendees: EventAttendee[];
  created_at: string;
  updated_at: string;
}

/** An overlapping event on the same person's calendar. Reported, not enforced. */
export interface ScheduleConflict {
  event_id: string;
  title: string;
  starts_at: string;
  ends_at: string;
}

export interface CalendarEventSaved {
  event: CalendarEvent;
  conflicts: ScheduleConflict[];
}

export interface CalendarEventInput {
  title: string;
  description?: string | null;
  location?: string | null;
  event_type?: CalendarEventType;
  status?: CalendarEventStatus;
  starts_at: string;
  ends_at: string;
  is_all_day?: boolean;
  reminder_minutes?: number | null;
  entity_type?: "lead" | "client" | "property" | "deal" | null;
  entity_id?: string | null;
  owner_id?: string | null;
  attendees?: Array<{
    user_id?: string | null;
    email?: string | null;
    display_name?: string | null;
  }>;
}

// --- MFA (Phase 3.7) ---

export interface MfaStatus {
  enabled: boolean;
  enrolled_at: string | null;
  recovery_codes_remaining: number;
  /** The caller's roles oblige enrolment and they have not done it. */
  setup_required: boolean;
}

/** The only response that ever contains the secret. */
export interface MfaEnrolmentStarted {
  secret: string;
  provisioning_uri: string;
}

/** Shown once, at generation. There is no endpoint to fetch them again. */
export interface MfaRecoveryCodes {
  recovery_codes: string[];
}

/**
 * The login response when a second factor is owed.
 *
 * Deliberately carries no profile: until the factor is proved the caller has
 * not authenticated, and a name would confirm the password was correct.
 */
export interface MfaChallengeRequired {
  mfa_required: true;
  challenge_token: string;
  expires_at: string;
}
