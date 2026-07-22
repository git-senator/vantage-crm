// Marks this module as server-only. If a "use client" component imports it,
// even transitively, the build fails instead of silently shipping data to the
// browser. See docs/SECURITY.md §4.
import "server-only";

import type {
  ActivityItem,
  SessionUser,
  CrmDocument,
  Task,
  TeamMember,
} from "@/types";

/**
 * Static fixtures for the prototype. Everything here is invented sample data —
 * no network, no persistence. Values are hand-picked rather than randomised so
 * the UI renders identically on the server and the client.
 */

export const team: Record<string, TeamMember> = {
  avery: { id: "u1", name: "Avery Chen", initials: "AC", role: "Managing Broker", hue: 268 },
  marcus: { id: "u2", name: "Marcus Delgado", initials: "MD", role: "Senior Agent", hue: 200 },
  priya: { id: "u3", name: "Priya Raman", initials: "PR", role: "Listing Agent", hue: 152 },
  jonah: { id: "u4", name: "Jonah Whitfield", initials: "JW", role: "Buyer's Agent", hue: 30 },
  sofia: { id: "u5", name: "Sofia Kowalski", initials: "SK", role: "Transaction Coordinator", hue: 340 },
  dmitri: { id: "u6", name: "Dmitri Volkov", initials: "DV", role: "Agent", hue: 78 },
};

export const currentUser: SessionUser = {
  ...team.avery,
  email: "avery.chen@vantagerealty.com",
};

export const contacts: Record<string, TeamMember> = {
  harper: { id: "c1", name: "Harper Lindqvist", initials: "HL", role: "Buyer", hue: 268 },
  omar: { id: "c2", name: "Omar Haddad", initials: "OH", role: "Seller", hue: 200 },
  yuki: { id: "c3", name: "Yuki Tanaka", initials: "YT", role: "Investor", hue: 152 },
  rosa: { id: "c4", name: "Rosa Villanueva", initials: "RV", role: "Buyer", hue: 30 },
  theo: { id: "c5", name: "Theo Bergström", initials: "TB", role: "Buyer", hue: 340 },
  nadia: { id: "c6", name: "Nadia Okonkwo", initials: "NO", role: "Seller", hue: 78 },
};

/* ------------------------------------------------------------------ tasks */

export const tasks: Task[] = [
  {
    id: "T-920",
    title: "Send comparable analysis to Harper",
    description: "Pull three closed comps within 0.5 miles of 1428 Sanchez.",
    status: "in-progress",
    priority: "high",
    dueDate: "Today, 4:00 PM",
    assignee: team.marcus,
    relatedTo: "D-410",
    list: "follow-up",
  },
  {
    id: "T-918",
    title: "Collect signed disclosure packet — Haddad",
    status: "blocked",
    priority: "urgent",
    dueDate: "Today, 6:00 PM",
    assignee: team.sofia,
    relatedTo: "D-408",
    list: "paperwork",
  },
  {
    id: "T-915",
    title: "Confirm Saturday open house staging",
    description: "Vendor walkthrough at 9am, photographer arrives 11am.",
    status: "todo",
    priority: "medium",
    dueDate: "Tomorrow",
    assignee: team.priya,
    relatedTo: "P-780",
    list: "showings",
  },
  {
    id: "T-911",
    title: "Draft July newsletter for past clients",
    status: "todo",
    priority: "low",
    dueDate: "Jul 24, 2026",
    assignee: team.dmitri,
    list: "marketing",
  },
  {
    id: "T-908",
    title: "Schedule inspection — 62 Laurel Grove",
    status: "todo",
    priority: "high",
    dueDate: "Jul 22, 2026",
    assignee: team.jonah,
    relatedTo: "D-402",
    list: "paperwork",
  },
  {
    id: "T-904",
    title: "Follow up with Ines Moreau on financing",
    status: "todo",
    priority: "medium",
    dueDate: "Jul 21, 2026",
    assignee: team.marcus,
    relatedTo: "L-2027",
    list: "follow-up",
  },
  {
    id: "T-899",
    title: "Upload final closing statement — Mbeki",
    status: "done",
    priority: "medium",
    dueDate: "Jul 3, 2026",
    assignee: team.sofia,
    relatedTo: "D-399",
    list: "paperwork",
  },
  {
    id: "T-895",
    title: "Order new listing photography — Folsom",
    status: "in-progress",
    priority: "medium",
    dueDate: "Jul 23, 2026",
    assignee: team.priya,
    relatedTo: "P-765",
    list: "marketing",
  },
  {
    id: "T-890",
    title: "Private showing prep — Bergström",
    status: "done",
    priority: "high",
    dueDate: "Jul 18, 2026",
    assignee: team.jonah,
    relatedTo: "D-392",
    list: "showings",
  },
];

/* --- calendar: ported to the live API in Phase 3.5; fixtures removed --- */

/* --- messages: ported to the live API in Phase 3.4; fixtures removed --- */

/* -------------------------------------------------------------- documents */

export const documents: CrmDocument[] = [
  {
    id: "DOC-501",
    name: "Purchase Agreement — 88 Townsend PH2.pdf",
    kind: "contract",
    size: "2.4 MB",
    owner: team.sofia,
    updatedAt: "2 hours ago",
    status: "awaiting-signature",
    relatedTo: "D-408",
  },
  {
    id: "DOC-498",
    name: "Seller Disclosure Packet — Haddad.pdf",
    kind: "disclosure",
    size: "8.1 MB",
    owner: team.sofia,
    updatedAt: "Yesterday",
    status: "signed",
    relatedTo: "D-408",
  },
  {
    id: "DOC-494",
    name: "Inspection Report — 62 Laurel Grove.pdf",
    kind: "inspection",
    size: "5.7 MB",
    owner: team.jonah,
    updatedAt: "Jul 18, 2026",
    status: "draft",
    relatedTo: "D-402",
  },
  {
    id: "DOC-490",
    name: "Listing Agreement — 2201 Folsom.docx",
    kind: "listing",
    size: "412 KB",
    owner: team.marcus,
    updatedAt: "Jul 16, 2026",
    status: "signed",
    relatedTo: "P-765",
  },
  {
    id: "DOC-486",
    name: "Closing Statement — Mbeki.pdf",
    kind: "financial",
    size: "1.1 MB",
    owner: team.sofia,
    updatedAt: "Jul 3, 2026",
    status: "signed",
    relatedTo: "D-399",
  },
  {
    id: "DOC-482",
    name: "Lead-Based Paint Disclosure — Sanchez.pdf",
    kind: "disclosure",
    size: "680 KB",
    owner: team.priya,
    updatedAt: "Jul 2, 2026",
    status: "expired",
    relatedTo: "P-780",
  },
  {
    id: "DOC-477",
    name: "Proof of Funds — Tanaka Holdings.pdf",
    kind: "financial",
    size: "240 KB",
    owner: team.avery,
    updatedAt: "Jun 29, 2026",
    status: "signed",
    relatedTo: "D-405",
  },
];

/* --- notifications: ported to the live API in Phase 3.3; fixture removed --- */

/* ---------------------------------------------------------------- activity */

export const activity: ActivityItem[] = [
  {
    id: "A-1",
    actor: team.avery,
    action: "moved a deal to",
    target: "Under contract",
    timestamp: "12m ago",
  },
  {
    id: "A-2",
    actor: team.priya,
    action: "logged a showing on",
    target: "1428 Sanchez Street",
    timestamp: "1h ago",
  },
  {
    id: "A-3",
    actor: team.sofia,
    action: "requested signatures on",
    target: "Purchase Agreement — 88 Townsend",
    timestamp: "2h ago",
  },
  {
    id: "A-4",
    actor: team.marcus,
    action: "added a note to",
    target: "Harper Lindqvist",
    timestamp: "3h ago",
  },
  {
    id: "A-5",
    actor: team.jonah,
    action: "created a task",
    target: "Schedule inspection — 62 Laurel Grove",
    timestamp: "5h ago",
  },
  {
    id: "A-6",
    actor: team.dmitri,
    action: "imported 14 leads from",
    target: "Instagram campaign",
    timestamp: "Yesterday",
  },
];

/* ------------------------------------------------------------------ charts */

export const revenueByMonth = [
  { month: "Jan", closed: 2.1, pipeline: 4.4 },
  { month: "Feb", closed: 2.8, pipeline: 5.1 },
  { month: "Mar", closed: 3.6, pipeline: 5.9 },
  { month: "Apr", closed: 3.1, pipeline: 6.8 },
  { month: "May", closed: 4.2, pipeline: 7.2 },
  { month: "Jun", closed: 5.4, pipeline: 8.1 },
  { month: "Jul", closed: 4.9, pipeline: 9.4 },
];

export const leadsBySource = [
  { source: "Zillow", count: 142 },
  { source: "Referral", count: 118 },
  { source: "Website", count: 96 },
  { source: "Open House", count: 64 },
  { source: "Instagram", count: 51 },
  { source: "Realtor.com", count: 37 },
];

export const conversionFunnel = [
  { stage: "Leads", value: 508 },
  { stage: "Contacted", value: 341 },
  { stage: "Qualified", value: 186 },
  { stage: "Touring", value: 94 },
  { stage: "Offer", value: 43 },
  { stage: "Closed", value: 27 },
];

export const dealMix = [
  { name: "Single Family", value: 38, fill: "var(--color-chart-1)" },
  { name: "Condo", value: 27, fill: "var(--color-chart-2)" },
  { name: "Multi-Family", value: 18, fill: "var(--color-chart-3)" },
  { name: "Commercial", value: 11, fill: "var(--color-chart-4)" },
  { name: "Land", value: 6, fill: "var(--color-chart-5)" },
];

export const agentLeaderboard = [
  { agent: team.avery, volume: 12400000, deals: 9, winRate: 42 },
  { agent: team.priya, volume: 8750000, deals: 11, winRate: 38 },
  { agent: team.marcus, volume: 7300000, deals: 7, winRate: 35 },
  { agent: team.jonah, volume: 5100000, deals: 8, winRate: 31 },
  { agent: team.dmitri, volume: 2400000, deals: 4, winRate: 22 },
];
