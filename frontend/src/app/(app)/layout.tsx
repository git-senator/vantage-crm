import { AppSidebar } from "@/components/layout/app-sidebar";
import { Topbar } from "@/components/layout/topbar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { currentUser, notifications } from "@/lib/mock-data";

/**
 * Server component. Resolves session data here and passes only what each client
 * component needs — the sidebar gets the user, the topbar gets an integer.
 *
 * Phase 1 swaps the fixture reads for a session lookup; the props do not change.
 */
export default function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <SidebarProvider>
      <AppSidebar user={currentUser} />
      <SidebarInset className="min-w-0">
        <Topbar unreadCount={unreadCount} />
        <main className="flex-1 p-4 md:p-6">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  );
}
