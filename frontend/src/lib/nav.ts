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

export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  /** Rendered as a count pill in the sidebar. */
  badge?: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const navigation: NavGroup[] = [
  {
    label: "Workspace",
    items: [
      { title: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
      { title: "AI Assistant", href: "/ai-assistant", icon: Sparkles, badge: "New" },
    ],
  },
  {
    label: "Pipeline",
    items: [
      { title: "Leads", href: "/leads", icon: Target, badge: "8" },
      { title: "Clients", href: "/clients", icon: Users },
      { title: "Properties", href: "/properties", icon: Building2 },
      { title: "Deals", href: "/deals", icon: Handshake, badge: "10" },
    ],
  },
  {
    label: "Work",
    items: [
      { title: "Calendar", href: "/calendar", icon: CalendarDays },
      { title: "Tasks", href: "/tasks", icon: FileText, badge: "6" },
      { title: "Messages", href: "/messages", icon: MessageSquare, badge: "3" },
      { title: "Documents", href: "/documents", icon: FileText },
    ],
  },
  {
    label: "Insights",
    items: [{ title: "Reports", href: "/reports", icon: BarChart3 }],
  },
  {
    label: "Automation",
    items: [{ title: "Workflows", href: "/automations", icon: Workflow }],
  },
];

export const secondaryNavigation: NavItem[] = [
  { title: "Notifications", href: "/notifications", icon: Bell, badge: "3" },
  { title: "Profile", href: "/profile", icon: CircleUser },
  { title: "Settings", href: "/settings", icon: Settings },
  // Admin-only. Listed here rather than in the sidebar groups because it is an
  // operator surface, not a place anyone works from day to day.
  { title: "System", href: "/settings/system", icon: Activity },
];

/** Flat lookup used by the topbar to title the current page. */
export const allNavItems: NavItem[] = [
  ...navigation.flatMap((group) => group.items),
  ...secondaryNavigation,
];
