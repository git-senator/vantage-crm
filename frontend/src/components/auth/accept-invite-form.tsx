"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle2, Loader2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTranslation } from "@/i18n/language-provider";
import {
  acceptInvitation,
  fetchInvitation,
  type InvitationInfo,
} from "@/lib/api/access-client";
import { ClientApiError } from "@/lib/api/client";

type Phase = "loading" | "invalid" | "form" | "done";

/**
 * Accept-invite flow: validate the link, let the person set a password, then
 * send them to sign in. Runs entirely client-side against the public endpoints;
 * no session exists yet.
 */
export function AcceptInviteForm({ token }: { token: string }) {
  const { t } = useTranslation();
  const router = useRouter();

  const [phase, setPhase] = useState<Phase>("loading");
  const [invite, setInvite] = useState<InvitationInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    fetchInvitation(token)
      .then((info) => {
        if (!active) return;
        setInvite(info);
        setPhase("form");
      })
      .catch(() => active && setPhase("invalid"));
    return () => {
      active = false;
    };
  }, [token]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const confirm = String(form.get("confirm") ?? "");
    if (password !== confirm) {
      setError(t("access.passwordMismatch"));
      return;
    }

    setPending(true);
    try {
      await acceptInvitation(token, password);
      setPhase("done");
      // Give the confirmation a beat to register, then hand off to sign-in.
      setTimeout(() => router.replace("/login"), 1600);
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : t("access.actionError"),
      );
      setPending(false);
    }
  }

  if (phase === "loading") {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {t("access.loading")}
      </div>
    );
  }

  if (phase === "invalid") {
    return (
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/8 p-4 text-sm">
          <TriangleAlert className="mt-0.5 size-5 shrink-0 text-destructive" />
          <div className="space-y-1">
            <p className="font-medium text-foreground">
              {t("access.inviteInvalidTitle")}
            </p>
            <p className="text-muted-foreground">
              {t("access.inviteInvalidBody")}
            </p>
          </div>
        </div>
        <Link
          href="/login"
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          {t("access.backToSignIn")}
        </Link>
      </div>
    );
  }

  if (phase === "done") {
    return (
      <div
        role="status"
        className="flex items-start gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/8 p-4 text-sm"
      >
        <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-emerald-600" />
        <div className="space-y-1">
          <p className="font-medium text-foreground">
            {t("access.acceptedTitle")}
          </p>
          <p className="text-muted-foreground">{t("access.acceptedBody")}</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("access.acceptTitle")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("access.acceptSubtitle", {
            org: invite?.organization_name ?? "",
            email: invite?.email ?? "",
          })}
        </p>
      </div>

      <form className="mt-8 space-y-4" onSubmit={handleSubmit} noValidate>
        {error && (
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="space-y-2">
          <Label htmlFor="password">{t("access.newPassword")}</Label>
          <Input
            id="password"
            name="password"
            type="password"
            required
            autoComplete="new-password"
            placeholder="••••••••••••"
            disabled={pending}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="confirm">{t("access.confirmPassword")}</Label>
          <Input
            id="confirm"
            name="confirm"
            type="password"
            required
            autoComplete="new-password"
            placeholder="••••••••••••"
            disabled={pending}
          />
        </div>

        <Button size="lg" className="w-full" type="submit" disabled={pending}>
          {pending ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              {t("access.settingPassword")}
            </>
          ) : (
            <>
              {t("access.setPassword")}
              <ArrowRight className="size-4" />
            </>
          )}
        </Button>
      </form>
    </>
  );
}
