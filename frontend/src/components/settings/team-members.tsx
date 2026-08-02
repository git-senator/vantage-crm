"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useTranslation } from "@/i18n/language-provider";
import { setMemberRole } from "@/lib/api/organization-client";
import type { OrganizationMember } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const ROLE_ORDER = ["owner", "admin", "manager", "agent"];
const ROLE_LABEL: Record<string, string> = {
  owner: "settings.roleOwner",
  admin: "settings.roleAdmin",
  manager: "settings.roleManager",
  agent: "settings.roleAgent",
};

/**
 * The real workspace roster.
 *
 * Members and their roles come from the API, not a fixture. Changing a member's
 * role revokes whatever they hold and grants the chosen one (roles are additive
 * server-side) and refreshes from the server, so what shows is always the
 * authoritative state — never an optimistic guess that could drift from RBAC.
 */
export function TeamMembers({
  members,
  canManage,
}: {
  members: OrganizationMember[];
  canManage: boolean;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);

  function primaryRole(roles: string[]): string {
    for (const key of ROLE_ORDER) {
      if (roles.includes(key)) return key;
    }
    return roles[0] ?? "agent";
  }

  async function change(member: OrganizationMember, next: string) {
    setBusyId(member.id);
    try {
      await setMemberRole(member.id, next, member.roles);
      router.refresh();
    } catch {
      toast.error(t("body.errGeneric"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      {members.map((member, index) => (
        <div
          key={member.id}
          className={cn(
            "flex items-center gap-3 px-5 py-3",
            index > 0 && "border-t",
          )}
        >
          <UserAvatar
            user={{
              id: member.id,
              name: member.full_name,
              initials: member.initials,
              role: "",
              hue: member.avatar_hue,
            }}
            size="sm"
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{member.full_name}</p>
            <p className="truncate text-xs text-muted-foreground">
              {member.job_title ?? member.email}
            </p>
          </div>
          <StatusBadge status={member.status} />
          <Select
            value={primaryRole(member.roles)}
            onValueChange={(v) => v && change(member, v)}
            disabled={!canManage || busyId === member.id}
          >
            <SelectTrigger size="sm" className="w-[120px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLE_ORDER.map((key) => (
                <SelectItem key={key} value={key}>
                  {t(ROLE_LABEL[key])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ))}
    </>
  );
}
