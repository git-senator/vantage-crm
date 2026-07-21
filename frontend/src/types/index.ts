/**
 * Domain types for the Vantage CRM prototype.
 *
 * These describe the shape of the mock fixtures only — there is no persistence
 * layer behind them. They exist so pages and components share one vocabulary.
 */

export type LeadStage =
  | "new"
  | "contacted"
  | "qualified"
  | "touring"
  | "unqualified";

export type LeadSource =
  | "Zillow"
  | "Website"
  | "Referral"
  | "Open House"
  | "Instagram"
  | "Cold Call"
  | "Realtor.com";

export type Temperature = "hot" | "warm" | "cold";

export interface Lead {
  id: string;
  name: string;
  email: string;
  phone: string;
  stage: LeadStage;
  source: LeadSource;
  temperature: Temperature;
  score: number;
  budget: [number, number];
  location: string;
  owner: TeamMember;
  createdAt: string;
  lastTouch: string;
  tags: string[];
}

// Clients now come from the API — see `Client` in lib/api/types.ts. The
// prototype fixture types are gone with the fixture.

// Properties now come from the API — see `Property` in lib/api/types.ts. The
// prototype fixture types are gone with the fixture.

export type DealStage =
  | "qualification"
  | "showing"
  | "offer"
  | "under-contract"
  | "closing"
  | "closed-won";

export interface Deal {
  id: string;
  title: string;
  client: string;
  property: string;
  value: number;
  commission: number;
  stage: DealStage;
  probability: number;
  closeDate: string;
  owner: TeamMember;
  priority: "low" | "medium" | "high";
}

export type TaskPriority = "low" | "medium" | "high" | "urgent";
export type TaskStatus = "todo" | "in-progress" | "blocked" | "done";

export interface Task {
  id: string;
  title: string;
  description?: string;
  status: TaskStatus;
  priority: TaskPriority;
  dueDate: string;
  assignee: TeamMember;
  relatedTo?: string;
  list: "follow-up" | "paperwork" | "showings" | "marketing";
}

export type EventKind = "showing" | "call" | "closing" | "open-house" | "internal";

export interface CalendarEvent {
  id: string;
  title: string;
  kind: EventKind;
  /** ISO date, YYYY-MM-DD. */
  date: string;
  start: string;
  end: string;
  location?: string;
  attendees: TeamMember[];
}

export interface TeamMember {
  id: string;
  name: string;
  initials: string;
  role: string;
  /** Deterministic hue for the generated avatar tint. */
  hue: number;
}

/**
 * The signed-in user. Passed from server layouts into client components as a
 * prop — never imported by a client component directly.
 *
 * Phase 1 resolves this from `GET /api/v1/auth/me` instead of a fixture; the
 * shape is intended to stay the same so consumers do not change.
 */
export interface SessionUser extends TeamMember {
  email: string;
}

export interface Conversation {
  id: string;
  contact: TeamMember;
  channel: "sms" | "email" | "whatsapp";
  preview: string;
  timestamp: string;
  unread: number;
  pinned?: boolean;
}

export interface Message {
  id: string;
  author: "me" | "them";
  body: string;
  timestamp: string;
  status?: "sent" | "delivered" | "read";
}

export type DocumentKind =
  | "contract"
  | "disclosure"
  | "inspection"
  | "listing"
  | "financial";

export interface CrmDocument {
  id: string;
  name: string;
  kind: DocumentKind;
  size: string;
  owner: TeamMember;
  updatedAt: string;
  status: "draft" | "awaiting-signature" | "signed" | "expired";
  relatedTo: string;
}

export interface Notification {
  id: string;
  title: string;
  body: string;
  timestamp: string;
  read: boolean;
  category: "deal" | "lead" | "task" | "system" | "mention";
}

export interface ActivityItem {
  id: string;
  actor: TeamMember;
  action: string;
  target: string;
  timestamp: string;
}
