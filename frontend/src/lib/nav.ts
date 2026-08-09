import {
  Activity,
  BarChart3,
  Bell,
  Building2,
  CalendarDays,
  CircleUser,
  FileText,
  Handshake,
  LayoutDashboard,
  MessageSquare,
  Settings,
  Sparkles,
  Target,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";

/**
 * Navigation is defined by **translation keys**, not literal labels. The
 * sidebar and topbar resolve `titleKey`/`labelKey` through `t()` at render
 * time, so the menu is in the user's language without this module knowing which
 * language that is. Hrefs and icons are language-independent and stay literal.
 */

export interface NavItem {
  /** Key into `nav.*` — resolved by the rendering component. */
  titleKey: string;
  href: string;
  icon: LucideIcon;
  /** A literal count pill (numbers are language-independent). */
  badge?: string;
  /** A translated pill, e.g. `common.new`. Takes precedence over `badge`. */
  badgeKey?: string;
  /**
   * Which attention count lights a dot on this item, if any.
   *
   * Declared here rather than matched on `href` in the sidebar, so adding a
   * dot to a new section is one field on the item that already exists — and so
   * nothing silently stops working when a route is renamed.
   */
  attention?: "messages" | "tasks" | "requests" | "notifications";
  /**
   * Permission required to see this item. When set, the sidebar hides the link
   * for a user who lacks it — so a role like `agent` never sees (or lands on) a
   * surface the API would 403. UX only; the route and API still enforce it.
   */
  permission?: string;
}

export interface NavGroup {
  /** Key into `nav.*` for the group heading. */
  labelKey: string;
  items: NavItem[];
}

export const navigation: NavGroup[] = [
  {
    labelKey: "nav.groupWorkspace",
    items: [
      { titleKey: "nav.dashboard", href: "/dashboard", icon: LayoutDashboard },
      {
        titleKey: "nav.aiAssistant",
        href: "/ai-assistant",
        icon: Sparkles,
        badgeKey: "common.new",
      },
    ],
  },
  {
    labelKey: "nav.groupPipeline",
    items: [
      { titleKey: "nav.leads", href: "/leads", icon: Target },
      { titleKey: "nav.clients", href: "/clients", icon: Users },
      { titleKey: "nav.properties", href: "/properties", icon: Building2 },
      { titleKey: "nav.deals", href: "/deals", icon: Handshake },
    ],
  },
  {
    labelKey: "nav.groupWork",
    items: [
      { titleKey: "nav.calendar", href: "/calendar", icon: CalendarDays },
      { titleKey: "nav.tasks", href: "/tasks", icon: FileText, attention: "tasks" },
      {
        titleKey: "nav.messages",
        href: "/messages",
        icon: MessageSquare,
        attention: "messages",
      },
      { titleKey: "nav.documents", href: "/documents", icon: FileText },
    ],
  },
  {
    labelKey: "nav.groupInsights",
    items: [{ titleKey: "nav.reports", href: "/reports", icon: BarChart3 }],
  },
  {
    labelKey: "nav.groupAutomation",
    items: [
      {
        titleKey: "nav.workflows",
        href: "/automations",
        icon: Workflow,
        permission: "automations.view",
      },
    ],
  },
];

export const secondaryNavigation: NavItem[] = [
  {
    titleKey: "nav.notifications",
    href: "/notifications",
    icon: Bell,
    attention: "notifications",
  },
  { titleKey: "nav.profile", href: "/profile", icon: CircleUser },
  { titleKey: "nav.settings", href: "/settings", icon: Settings },
  // Admin-only. Listed here rather than in the sidebar groups because it is an
  // operator surface, not a place anyone works from day to day.
  { titleKey: "nav.system", href: "/settings/system", icon: Activity },
];

/** Flat lookup used by the topbar to title the current page. */
export const allNavItems: NavItem[] = [
  ...navigation.flatMap((group) => group.items),
  ...secondaryNavigation,
];
