import { AppSidebar } from "@/components/layout/app-sidebar";
import { Topbar } from "@/components/layout/topbar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { fetchUnreadCount } from "@/lib/api/notifications";
import { hasPermission, requireSession } from "@/lib/auth/session";
import type { SessionUser } from "@/types";

/**
 * Authenticated shell.
 *
 * Server component. Resolves the session here and passes only what each client
 * component needs — the sidebar gets a user, the topbar gets an integer. A
 * client component must never import a data module (docs/SECURITY.md §4).
 *
 * `requireSession` is the second authorization layer. Middleware already
 * redirected unauthenticated navigation, but it only checks cookie *presence*;
 * a forged or expired token reaches here and is rejected by the API.
 */
export default async function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const session = await requireSession();

  const user: SessionUser = {
    id: session.id,
    name: session.full_name,
    initials: session.initials,
    role: session.job_title ?? "Member",
    hue: session.avatar_hue,
    email: session.email,
  };

  // One integer, from an endpoint that returns exactly that. The topbar never
  // sees a notification body, which is the boundary rule this layout exists to
  // enforce (docs/SECURITY.md §4).
  const unreadCount = await fetchUnreadCount();

  return (
    <SidebarProvider>
      <AppSidebar
        user={user}
        canManageUsers={hasPermission(session, "users.manage")}
      />
      <SidebarInset className="min-w-0">
        <Topbar unreadCount={unreadCount} />
        <main className="flex-1 p-4 md:p-6">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  );
}
