"use client";

import { useState } from "react";
import { Globe, KeyRound, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ClientApiError } from "@/lib/api/client";
import { changePassword, signOutEverywhere } from "@/lib/api/security-client";
import { useTranslation } from "@/i18n/language-provider";
import { cn } from "@/lib/utils";

/**
 * The account-security actions, wired.
 *
 * Both do the real thing and then bounce to the login page, because both end
 * the current session server-side — a password change revokes every session by
 * design, and "sign out everywhere" is that on purpose. Redirecting is not
 * optional polish; the cookie is already dead.
 */
export function SecurityActions() {
  const { t } = useTranslation();
  const [pwOpen, setPwOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);

  async function submitPassword() {
    if (next !== confirm) {
      setError(t("settings.pwMismatch"));
      return;
    }
    setPending(true);
    setError(null);
    try {
      await changePassword(current, next);
      toast.success(t("settings.pwChanged"));
      window.location.href = "/login";
    } catch (caught) {
      setError(
        caught instanceof ClientApiError ? caught.message : t("body.errGeneric"),
      );
      setPending(false);
    }
  }

  async function revoke() {
    setRevoking(true);
    try {
      await signOutEverywhere();
      toast.success(t("settings.sessionsRevoked"));
      window.location.href = "/login";
    } catch {
      toast.error(t("body.errGeneric"));
      setRevoking(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.security")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* -------------------------------------------------- password */}
        <div className="flex items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <KeyRound className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{t("settings.passwordLabel")}</p>
            <p className="text-xs text-muted-foreground">
              {t("settings.passwordDetail")}
            </p>
          </div>
          <Dialog open={pwOpen} onOpenChange={setPwOpen}>
            <DialogTrigger
              render={
                <Button variant="outline" size="sm">
                  {t("buttons.change")}
                </Button>
              }
            />
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>{t("settings.changePassword")}</DialogTitle>
                <DialogDescription>
                  {t("settings.changePasswordDesc")}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                {error && (
                  <p role="alert" className="text-sm text-destructive">
                    {error}
                  </p>
                )}
                <div className="space-y-2">
                  <Label htmlFor="pw-current">
                    {t("settings.currentPassword")}
                  </Label>
                  <Input
                    id="pw-current"
                    type="password"
                    autoComplete="current-password"
                    value={current}
                    onChange={(e) => setCurrent(e.target.value)}
                    disabled={pending}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="pw-new">{t("settings.newPassword")}</Label>
                  <Input
                    id="pw-new"
                    type="password"
                    autoComplete="new-password"
                    value={next}
                    onChange={(e) => setNext(e.target.value)}
                    disabled={pending}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="pw-confirm">
                    {t("settings.confirmPassword")}
                  </Label>
                  <Input
                    id="pw-confirm"
                    type="password"
                    autoComplete="new-password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    disabled={pending}
                  />
                </div>
              </div>
              <DialogFooter>
                <DialogClose
                  render={
                    <Button variant="ghost" disabled={pending}>
                      {t("buttons.cancel")}
                    </Button>
                  }
                />
                <Button
                  onClick={submitPassword}
                  disabled={pending || !current || next.length < 8}
                >
                  {pending && <Loader2 className="size-4 animate-spin" />}
                  {t("settings.changePassword")}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>

        {/* --------------------------------------------------- sessions */}
        <div className={cn("flex items-center gap-3 border-t pt-4")}>
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <Globe className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">
              {t("settings.activeSessions")}
            </p>
            <p className="text-xs text-muted-foreground">
              {t("settings.activeSessionsDetail")}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={revoke}
            disabled={revoking}
          >
            {revoking && <Loader2 className="size-4 animate-spin" />}
            {t("settings.signOutEverywhere")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
