"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Check, Inbox, Loader2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslation } from "@/i18n/language-provider";
import {
  approveAccessRequest,
  listAccessRequests,
  rejectAccessRequest,
  type AccessRequest,
  type AssignableRole,
} from "@/lib/api/access-client";
import { ClientApiError } from "@/lib/api/client";

const ROLES: AssignableRole[] = ["agent", "manager", "admin"];

export function RequestsQueue() {
  const { t } = useTranslation();

  const [requests, setRequests] = useState<AccessRequest[] | null>(null);
  const [roles, setRoles] = useState<Record<string, AssignableRole>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listAccessRequests()
      .then((rows) => active && setRequests(rows))
      .catch(() => active && setRequests([]));
    return () => {
      active = false;
    };
  }, []);

  function patch(updated: AccessRequest) {
    setRequests((prev) =>
      (prev ?? []).map((r) => (r.id === updated.id ? updated : r)),
    );
  }

  async function onApprove(request: AccessRequest) {
    setBusyId(request.id);
    try {
      const updated = await approveAccessRequest(
        request.id,
        roles[request.id] ?? "agent",
      );
      patch(updated);
      toast.success(t("access.grantedToast"));
    } catch (caught) {
      toast.error(
        caught instanceof ClientApiError ? caught.message : t("access.actionError"),
      );
    } finally {
      setBusyId(null);
    }
  }

  async function onReject(request: AccessRequest) {
    setBusyId(request.id);
    try {
      const updated = await rejectAccessRequest(request.id);
      patch(updated);
      toast.success(t("access.rejectedToast"));
    } catch (caught) {
      toast.error(
        caught instanceof ClientApiError ? caught.message : t("access.actionError"),
      );
    } finally {
      setBusyId(null);
    }
  }

  if (requests === null) {
    return (
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (requests.length === 0) {
    return (
      <Card className="flex flex-col items-center gap-3 p-12 text-center">
        <Inbox className="size-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">{t("access.empty")}</p>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {requests.map((request) => {
        const isPending = request.status === "pending";
        const isBusy = busyId === request.id;
        return (
          <Card key={request.id} className="p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{request.full_name}</span>
                  <StatusPill status={request.status} />
                </div>
                <p className="truncate text-sm text-muted-foreground">
                  {request.email}
                  {request.requested_role ? ` · ${request.requested_role}` : ""}
                </p>
                {request.message && (
                  <p className="text-sm text-muted-foreground/90">
                    “{request.message}”
                  </p>
                )}
              </div>

              {isPending && (
                <div className="flex shrink-0 items-center gap-2">
                  <Select
                    value={roles[request.id] ?? "agent"}
                    onValueChange={(next) =>
                      setRoles((prev) => ({
                        ...prev,
                        [request.id]: (next ?? "agent") as AssignableRole,
                      }))
                    }
                    disabled={isBusy}
                  >
                    <SelectTrigger className="w-[130px]" aria-label={t("access.chooseRole")}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ROLES.map((role) => (
                        <SelectItem key={role} value={role}>
                          {t(`access.role${role[0].toUpperCase()}${role.slice(1)}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  <Button size="sm" disabled={isBusy} onClick={() => onApprove(request)}>
                    {isBusy ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Check className="size-4" />
                    )}
                    {t("access.approve")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={isBusy}
                    onClick={() => onReject(request)}
                  >
                    <X className="size-4" />
                    {t("access.reject")}
                  </Button>
                </div>
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function StatusPill({ status }: { status: AccessRequest["status"] }) {
  const { t } = useTranslation();
  if (status === "approved") {
    return (
      <Badge variant="secondary" className="text-emerald-700 dark:text-emerald-400">
        {t("access.statusApproved")}
      </Badge>
    );
  }
  if (status === "rejected") {
    return <Badge variant="outline">{t("access.statusRejected")}</Badge>;
  }
  return <Badge>{t("access.statusPending")}</Badge>;
}
