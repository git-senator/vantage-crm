"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Building2,
  CalendarPlus,
  ChevronsUpDown,
  Handshake,
  Inbox,
  Plus,
  Settings,
  Target,
  UserRound,
  Users,
} from "lucide-react";

import { SignOutItem } from "@/components/auth/sign-out-item";
import { AttentionDot } from "@/components/layout/attention-dot";
import { LanguageMenu } from "@/components/layout/language-menu";
import { BrandLockup } from "@/components/shared/brand";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";
import { useTranslation } from "@/i18n/language-provider";
import { useAttention } from "@/lib/api/attention-client";
import { navigation, secondaryNavigation } from "@/lib/nav";
import type { SessionUser } from "@/types";

/**
 * What the header's Create button offers. Each row carries the permission its
 * own page already enforces, so the menu lists only what the user can actually
 * create. With no rows left the button is hidden: an empty menu misleads the
 * same way the unwired button did.
 */
const CREATE_TARGETS = [
  { href: "/leads/new", labelKey: "body.newLead", permission: "leads.manage", icon: Target },
  { href: "/clients/new", labelKey: "body.newClient", permission: "contacts.manage", icon: Users },
  { href: "/properties/new", labelKey: "body.newListing", permission: "properties.manage", icon: Building2 },
  { href: "/deals/new", labelKey: "body.newDeal", permission: "deals.manage", icon: Handshake },
  // An event has no page of its own — the calendar opens it in a dialog.
  { href: "/calendar?new=1", labelKey: "body.calNewEvent", permission: "activities.manage", icon: CalendarPlus },
] as const;

/**
 * The signed-in user arrives as a prop from the server layout. In Phase 1 that
 * layout resolves it from the session rather than a fixture; this component
 * does not change.
 */
export function AppSidebar({
  user,
  canManageUsers = false,
  permissions = [],
}: {
  user: SessionUser;
  /** Reveals the access-request queue. UX only — the route and API enforce it. */
  canManageUsers?: boolean;
  /** The signed-in user's permissions, used to hide nav items they can't reach. */
  permissions?: readonly string[];
}) {
  const pathname = usePathname();
  const { t } = useTranslation();
  const attention = useAttention();
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  const canSee = (item: { permission?: string }) =>
    !item.permission || permissions.includes(item.permission);

  const createTargets = CREATE_TARGETS.filter(canSee);

  // Drop items the user lacks permission for, then any group left empty.
  const visibleGroups = navigation
    .map((group) => ({ ...group, items: group.items.filter(canSee) }))
    .filter((group) => group.items.length > 0);

  return (
    <Sidebar collapsible="icon" className="border-r">
      <SidebarHeader className="gap-3 px-3 py-4">
        <Link href="/dashboard" className="group-data-[collapsible=icon]:hidden">
          <BrandLockup />
        </Link>
        {createTargets.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button className="w-full justify-start gap-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
                  <Plus className="size-4" />
                  <span className="group-data-[collapsible=icon]:hidden">
                    {t("buttons.create")}
                  </span>
                </Button>
              }
            />
            <DropdownMenuContent align="start" className="w-56">
              {createTargets.map((target) => (
                <DropdownMenuItem
                  key={target.href}
                  render={<Link href={target.href} />}
                >
                  <target.icon className="size-4" />
                  {t(target.labelKey)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </SidebarHeader>

      <SidebarContent className="scrollbar-slim px-1">
        {visibleGroups.map((group) => (
          <SidebarGroup key={group.labelKey}>
            <SidebarGroupLabel>{t(group.labelKey)}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => {
                  const title = t(item.titleKey);
                  const badge = item.badgeKey ? t(item.badgeKey) : item.badge;
                  const waiting = item.attention ? attention[item.attention] : 0;
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={isActive(item.href)}
                        tooltip={title}
                        render={
                          <Link href={item.href}>
                            <item.icon />
                            <span>{title}</span>
                          </Link>
                        }
                      />
                      {/* A pill and a dot would fight for the same slot; the
                          pill is the louder of the two, so it wins. */}
                      {badge ? (
                        <SidebarMenuBadge>{badge}</SidebarMenuBadge>
                      ) : waiting > 0 ? (
                        <SidebarMenuBadge>
                          <AttentionDot count={waiting} />
                        </SidebarMenuBadge>
                      ) : null}
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}

        {canManageUsers && (
          <SidebarGroup>
            <SidebarGroupLabel>{t("nav.system")}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    isActive={isActive("/requests")}
                    tooltip={t("nav.requests")}
                    render={
                      <Link href="/requests">
                        <Inbox />
                        <span>{t("nav.requests")}</span>
                      </Link>
                    }
                  />
                  {attention.requests > 0 && (
                    <SidebarMenuBadge>
                      <AttentionDot count={attention.requests} />
                    </SidebarMenuBadge>
                  )}
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
      </SidebarContent>

      <SidebarFooter className="gap-2">
        <SidebarMenu>
          {secondaryNavigation.map((item) => {
            const waiting = item.attention ? attention[item.attention] : 0;
            return (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  isActive={isActive(item.href)}
                  tooltip={t(item.titleKey)}
                  size="sm"
                  render={
                    <Link href={item.href}>
                      <item.icon />
                      <span>{t(item.titleKey)}</span>
                    </Link>
                  }
                />
                {waiting > 0 && (
                  <SidebarMenuBadge>
                    <AttentionDot count={waiting} />
                  </SidebarMenuBadge>
                )}
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>

        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <button className="flex w-full items-center gap-2 rounded-lg p-1.5 text-left transition-colors hover:bg-sidebar-accent">
                <UserAvatar user={user} size="sm" />
                <span className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
                  <span className="block truncate text-sm font-medium">
                    {user.name}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {user.role}
                  </span>
                </span>
                <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground group-data-[collapsible=icon]:hidden" />
              </button>
            }
          />
          <DropdownMenuContent side="top" align="start" className="w-56">
            <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
              {user.email}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem render={<Link href="/profile" />}>
              <UserRound className="size-4" />
              {t("nav.profile")}
            </DropdownMenuItem>
            <DropdownMenuItem render={<Link href="/settings" />}>
              <Settings className="size-4" />
              {t("nav.settings")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <LanguageMenu />
            <DropdownMenuSeparator />
            <SignOutItem />
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  );
}
